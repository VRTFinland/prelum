import io
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.errors import TooManyOutputFilesError
from app.main import app
from app.models import RenderFile, RenderRequest
from app.render.renderer import RenderResult, TypstRenderer

client = TestClient(app)
TOKEN = {"X-Prelum-Api-Token": "dev-only-insecure-token"}


@pytest.mark.parametrize("output_format", ["pdf", "svg", "png"])
def test_render_request_accepts_every_output_format(output_format: str):
    request = RenderRequest.model_validate({"source": '#text("hello")', "output": {"format": output_format}})

    assert request.output.format == output_format


def test_openapi_discriminates_output_formats_and_keeps_source_required():
    schemas = app.openapi()["components"]["schemas"]

    assert schemas["RenderOutput"]["discriminator"] == {
        "propertyName": "format",
        "mapping": {
            "pdf": "#/components/schemas/PdfOutput",
            "png": "#/components/schemas/PngOutput",
            "svg": "#/components/schemas/SvgOutput",
        },
    }
    assert schemas["RenderRequest"]["required"] == ["source"]
    assert "archive" in schemas["PngOutput"]["properties"]
    assert "archive" in schemas["SvgOutput"]["properties"]
    assert "archive" not in schemas["PdfOutput"]["properties"]


def test_render_request_rejects_unknown_output_fields():
    with pytest.raises(ValidationError):
        RenderRequest.model_validate({"source": '#text("hello")', "output": {"format": "pdf", "fileName": "wrong.pdf"}})


def test_v1_rejects_an_unsupported_format_with_its_published_code():
    response = client.post(
        "/v1/render",
        json={"source": '#text("hello")', "output": {"format": "docx"}},
        headers=TOKEN,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "unsupported_format"


def test_render_request_requires_non_empty_source():
    with pytest.raises(ValidationError):
        RenderRequest(source="")


def test_render_file_requires_explicit_encoding():
    with pytest.raises(ValidationError):
        RenderFile(content="hello")


def test_render_request_rejects_file_string_shorthand():
    with pytest.raises(ValidationError):
        RenderRequest(source='#text("hello")', files={"lib/x.typ": "content"})


def test_only_v1_render_route_exists():
    response = client.post("/render", json={"source": '#text("hello")'}, headers=TOKEN)

    assert response.status_code == 404


def test_v1_route_builds_one_render_job():
    fake_result = RenderResult(bytes=b"%PDF-1.4", content_type="application/pdf", filename="report.pdf")
    payload = {
        "source": '#text("hello")',
        "files": {"lib/x.typ": {"encoding": "text", "content": "#let x = 1"}},
        "data": None,
        "output": {"format": "pdf", "filename": "report.pdf"},
    }

    with patch.object(TypstRenderer, "render", new=AsyncMock(return_value=fake_result)) as render:
        response = client.post("/v1/render", json=payload, headers=TOKEN)

    assert response.status_code == 200
    job = render.await_args.args[0]
    assert job.source == payload["source"]
    assert job.data is None
    assert job.output.filename == "report.pdf"
    assert job.files["lib/x.typ"].content == "#let x = 1"


def test_v1_returns_the_published_output_file_limit_error():
    with patch.object(
        TypstRenderer,
        "render",
        new=AsyncMock(side_effect=TooManyOutputFilesError(count=3, max_allowed=2)),
    ):
        response = client.post(
            "/v1/render",
            json={"source": '#text("hello")', "output": {"format": "png", "archive": "zip"}},
            headers=TOKEN,
        )

    assert response.status_code == 413
    assert response.json()["code"] == "too_many_output_files"


def test_v1_validation_errors_use_problem_json_without_echoing_input():
    response = client.post(
        "/v1/render",
        json={"source": "SECRETMARKER", "files": {"../bad.typ": {"encoding": "text", "content": "x"}}},
        headers=TOKEN,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_template_path"
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "SECRETMARKER" not in response.text


@pytest.mark.parametrize(
    "files",
    [
        {
            "Logo.png": {"encoding": "text", "content": "x"},
            "logo.png": {"encoding": "text", "content": "y"},
        },
        {
            "lib": {"encoding": "text", "content": "x"},
            "lib/x.typ": {"encoding": "text", "content": "y"},
        },
    ],
)
def test_v1_classifies_colliding_file_keys_as_invalid_file_data(files: dict[str, object]):
    response = client.post(
        "/v1/render",
        json={"source": '#text("hello")', "files": files},
        headers=TOKEN,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_file_data"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("data_fragment", "typst_type"),
    [
        ({}, "dictionary"),
        ({"data": []}, "array"),
    ],
)
def test_v1_preserves_empty_data_shape(data_fragment: dict[str, object], typst_type: str):
    payload = {
        "source": f'#assert(type(data) == {typst_type})\n#text("correct shape")',
        **data_fragment,
    }

    response = client.post("/v1/render", json=payload, headers=TOKEN)

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("output_format", "content_type", "prefix"),
    [
        ("svg", "image/svg+xml", b"<svg "),
        ("png", "image/png", b"\x89PNG\r\n\x1a\n"),
    ],
)
def test_v1_renders_single_page_image_formats(output_format: str, content_type: str, prefix: bytes):
    response = client.post(
        "/v1/render",
        json={"source": '#text("one page")', "output": {"format": output_format}},
        headers=TOKEN,
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    assert response.content.startswith(prefix)


@pytest.mark.integration
@pytest.mark.parametrize("output_format", ["svg", "png"])
def test_v1_rejects_multi_page_image_output(output_format: str):
    response = client.post(
        "/v1/render",
        json={"source": '#text("first")\n#pagebreak()\n#text("second")', "output": {"format": output_format}},
        headers=TOKEN,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "template_compile_failed"


@pytest.mark.integration
@pytest.mark.parametrize("output_format", ["svg", "png"])
def test_v1_selects_one_page_for_image_output(output_format: str):
    response = client.post(
        "/v1/render",
        json={
            "source": '#text("first")\n#pagebreak()\n#text("second")',
            "output": {"format": output_format, "page": 2},
        },
        headers=TOKEN,
    )

    assert response.status_code == 200


@pytest.mark.integration
def test_v1_returns_selected_png_pages_as_a_zip_at_the_requested_ppi():
    source = (
        "#set page(width: 72pt, height: 72pt, margin: 0pt)\n"
        "#rect(width: 72pt, height: 72pt, fill: red)\n#pagebreak()\n"
        "#rect(width: 72pt, height: 72pt, fill: green)\n#pagebreak()\n"
        "#rect(width: 72pt, height: 72pt, fill: blue)"
    )

    response = client.post(
        "/v1/render",
        json={
            "source": source,
            "output": {"format": "png", "archive": "zip", "pages": "1,3", "ppi": 72},
        },
        headers=TOKEN,
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == 'attachment; filename="rendered.zip"'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["page-01.png", "page-03.png"]
        for name in archive.namelist():
            image = archive.read(name)
            assert image.startswith(b"\x89PNG\r\n\x1a\n")
            assert int.from_bytes(image[16:20]) == 72


@pytest.mark.integration
def test_v1_returns_all_svg_pages_when_archive_pages_are_omitted():
    response = client.post(
        "/v1/render",
        json={
            "source": '#text("first")\n#pagebreak()\n#text("second")\n#pagebreak()\n#text("third")',
            "output": {"format": "svg", "archive": "zip"},
        },
        headers=TOKEN,
    )

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["page-01.svg", "page-02.svg", "page-03.svg"]
        assert all(archive.read(name).startswith(b"<svg ") for name in archive.namelist())


@pytest.mark.integration
def test_v1_image_archive_supports_an_open_page_range():
    response = client.post(
        "/v1/render",
        json={
            "source": '#text("first")\n#pagebreak()\n#text("second")\n#pagebreak()\n#text("third")',
            "output": {"format": "svg", "archive": "zip", "pages": "2-"},
        },
        headers=TOKEN,
    )

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["page-02.svg", "page-03.svg"]


@pytest.mark.integration
@pytest.mark.parametrize("archive", [False, True])
def test_v1_classifies_a_missing_selected_image_page_as_caller_input(archive: bool):
    output: dict[str, object] = {"format": "svg"}
    output.update({"archive": "zip", "pages": "99"} if archive else {"page": 99})

    response = client.post(
        "/v1/render",
        json={"source": '#text("only page")', "output": output},
        headers=TOKEN,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "template_compile_failed"


@pytest.mark.integration
def test_v1_png_ppi_controls_pixel_dimensions():
    source = "#set page(width: 72pt, height: 72pt, margin: 0pt)\n#rect(width: 72pt, height: 72pt)"

    low = client.post(
        "/v1/render",
        json={"source": source, "output": {"format": "png", "ppi": 72}},
        headers=TOKEN,
    )
    high = client.post(
        "/v1/render",
        json={"source": source, "output": {"format": "png", "ppi": 144}},
        headers=TOKEN,
    )

    assert low.status_code == high.status_code == 200
    assert int.from_bytes(low.content[16:20]) == 72
    assert int.from_bytes(high.content[16:20]) == 144


@pytest.mark.integration
def test_v1_png_renders_a_thumbnail_at_low_ppi():
    source = "#set page(width: 720pt, height: 720pt, margin: 0pt)\n#rect(width: 720pt, height: 720pt)"

    response = client.post(
        "/v1/render",
        json={"source": source, "output": {"format": "png", "ppi": 10}},
        headers=TOKEN,
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert int.from_bytes(response.content[16:20]) == 100
    assert int.from_bytes(response.content[20:24]) == 100


@pytest.mark.integration
def test_v1_enforces_a_pdf_standard():
    response = client.post(
        "/v1/render",
        json={
            "source": '#set document(title: "Archive")\n#text("archivable")',
            "output": {"format": "pdf", "standards": ["a-2b"]},
        },
        headers=TOKEN,
    )

    assert response.status_code == 200
    assert b"pdfaid:part" in response.content


@pytest.mark.integration
def test_v1_selects_pdf_pages_and_version():
    response = client.post(
        "/v1/render",
        json={
            "source": '#text("first")\n#pagebreak()\n#text("second")',
            "output": {"format": "pdf", "version": "1.4", "pages": "2"},
        },
        headers=TOKEN,
    )

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-1.4")
