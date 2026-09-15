import io
import json
import zipfile
from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.errors import RenderError, TooManyOutputFilesError
from app.deps import get_renderer
from app.main import app
from app.models import RenderFile, RenderRequest
from app.render.renderer import RenderResult, TypstRenderer

client = TestClient(app)
TOKEN = {"X-Prelum-Api-Token": "dev-only-insecure-token"}


@pytest.mark.parametrize("codepoint", [0xD800, 0xDFFF])
@pytest.mark.parametrize("oversized_key", [False, True])
def test_v1_string_limit_with_surrogate_key_returns_a_problem(
    make_renderer: Callable[..., TypstRenderer], codepoint: int, oversized_key: bool
):
    renderer = make_renderer(max_string_bytes=1024)
    key = chr(codepoint) + ("k" * 1024 if oversized_key else "")
    value = "fine" if oversized_key else "x" * 1025
    body = json.dumps({"source": "hello", "data": {"outer": [{key: value}]}})
    app.dependency_overrides[get_renderer] = lambda: renderer
    try:
        response = client.post("/v1/render", content=body, headers={**TOKEN, "Content-Type": "application/json"})
    finally:
        del app.dependency_overrides[get_renderer]

    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = response.json()
    assert problem["code"] == "string_too_large"
    escaped_key = f"\\u{codepoint:04x}"
    expected_key = escaped_key + "k" * 74 + "…" if oversized_key else escaped_key
    assert problem["context"] == {
        "path": ["outer", 0, expected_key],
        # Both cases end at the same path; only this says which of the two strings is too long.
        "subject": "key" if oversized_key else "value",
        "size": 1027 if oversized_key else 1025,
        "limit": 1024,
    }


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


@pytest.mark.parametrize(
    ("output", "rule"),
    [
        ({"format": "pdf", "standards": ["a-2b", "a-2b"]}, "duplicate_standards"),
        ({"format": "pdf", "standards": ["a-2b", "a-3b"]}, "multiple_pdf_a_standards"),
        ({"format": "pdf", "standards": ["a-4", "ua-1"]}, "ua_1_with_pdf_a_4"),
        ({"format": "pdf", "version": "1.4", "standards": ["a-2b"]}, "version_conflicts_with_standard"),
        ({"format": "pdf", "version": "2.0", "standards": ["ua-1"]}, "ua_1_with_pdf_2_0"),
        ({"format": "pdf", "standards": ["a-1a"], "pages": "1-2"}, "pages_with_tagged_standard"),
        ({"format": "png", "pages": "1-2"}, "pages_requires_archive"),
        ({"format": "png", "archive": "zip", "page": 1}, "page_with_archive"),
        ({"format": "pdf", "pages": "1" + ",1" * 200}, "page_selection_too_long"),
        ({"format": "pdf", "pages": ",".join(["1"] * 65)}, "page_selection_too_many_segments"),
        ({"format": "pdf", "pages": "1-a"}, "page_selection_malformed"),
        ({"format": "pdf", "pages": "3-2"}, "page_range_end_precedes_start"),
    ],
)
def test_v1_names_the_output_rule_that_rejected_the_request(output: dict[str, object], rule: str):
    """
    Every one of these answers `invalid_request` at the same `loc`, so `rule` is the only difference.

    Without it a caller distinguishing "you asked for two PDF/A profiles" from "that version
    contradicts your profile" has nothing but `msg`, which docs/api/errors.md reserves the right to
    reword. This is the assertion an SDK's own conformance run makes.
    """
    response = client.post("/v1/render", json={"source": '#text("hello")', "output": output}, headers=TOKEN)

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "invalid_request"
    assert body["context"]["rule"] == rule


def test_v1_output_rule_rejection_keeps_the_published_validation_error_shape():
    """
    The id had to arrive as a new context key, not as a new `errors[].type`.

    `type` is published, and an existing field changing meaning needs a new API version — so a
    client pinned to `value_error` must keep matching after the ids were introduced.
    """
    response = client.post(
        "/v1/render",
        json={"source": '#text("hello")', "output": {"format": "png", "pages": "1-2"}},
        headers=TOKEN,
    )

    body = response.json()
    error = body["context"]["errors"][0]
    assert error["type"] == "value_error"
    assert error["loc"] == ["body", "output", "png"]
    assert set(body["context"]) == {"errors", "rule"}


@pytest.mark.parametrize(
    ("payload", "expected_detail_prefix"),
    [
        ({"output": {"format": "png", "pages": "1-2"}}, "source:"),
        (
            {
                "source": '#text("hello")',
                "output": {"format": "pdf", "standards": ["a-1b", "a-2b", "ua-1"], "pages": "1-a"},
            },
            "output.pdf.standards:",
        ),
    ],
)
def test_v1_reports_no_rule_for_a_failure_the_rules_do_not_name(
    payload: dict[str, object], expected_detail_prefix: str
):
    """
    `rule` describes the failure `detail` reports, never whichever error happens to carry an id.

    Both of these break an output rule *and* something with no id — a missing source, and three
    standards where two are allowed. Lifting the id from any error that had one produced
    "source: Field required" beside a rule about the page selection, so a client following the
    documentation's advice to branch on `rule` reported a cause the response was not about.
    """
    response = client.post("/v1/render", json=payload, headers=TOKEN)

    assert response.status_code == 400
    body = response.json()
    assert body["detail"].startswith(expected_detail_prefix)
    assert "rule" not in body["context"]


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
        new=AsyncMock(side_effect=TooManyOutputFilesError(limit=2)),
    ):
        response = client.post(
            "/v1/render",
            json={"source": '#text("hello")', "output": {"format": "png", "archive": "zip"}},
            headers=TOKEN,
        )

    assert response.status_code == 413
    assert response.json()["code"] == "too_many_output_files"
    assert response.json()["context"] == {"limit": 2}


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

    body = response.json()
    assert isinstance(body["detail"], str)
    assert set(body["context"]) == {"errors"}
    assert "key" not in body["context"]


@pytest.mark.parametrize(
    ("files", "expected_key"),
    [
        (
            {
                "Logo.png": {"encoding": "text", "content": "x"},
                "logo.png": {"encoding": "text", "content": "y"},
            },
            "logo.png",
        ),
        (
            {
                "lib": {"encoding": "text", "content": "x"},
                "lib/x.typ": {"encoding": "text", "content": "y"},
            },
            "lib/x.typ",
        ),
    ],
)
def test_v1_classifies_colliding_file_keys_as_invalid_file_data(files: dict[str, object], expected_key: str):
    response = client.post(
        "/v1/render",
        json={"source": '#text("hello")', "files": files},
        headers=TOKEN,
    )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "invalid_file_data"
    assert isinstance(body["detail"], str)
    assert body["context"]["key"] == expected_key
    assert [error["type"] for error in body["context"]["errors"]] == ["invalid_file_data"]
    assert set(body["context"]) == {"errors", "key", "rule"}


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


def test_v1_problem_responses_always_carry_context():
    response = client.post("/v1/render", json={"source": '#text("hello")'}, headers={"X-Prelum-Api-Token": "wrong"})

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
    assert response.json()["context"] == {}


def test_v1_renderer_raised_invalid_file_data_carries_only_its_own_fields():
    """This never reaches Pydantic, so no `errors` list exists to publish — docs/api/errors.md says so."""
    files = {"asset.png": {"encoding": "base64", "content": "not!base64"}}
    response = client.post("/v1/render", json={"source": '#text("hello")', "files": files}, headers=TOKEN)

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "invalid_file_data"
    assert body["context"] == {"key": "asset.png"}


def test_v1_unencodable_file_content_reports_its_key_without_an_errors_list():
    """A lone surrogate is valid JSON but has no UTF-8 form, so the body is sent as raw bytes."""
    body = rb'{"source": "#text(\"hello\")", "files": {"x.typ": {"encoding": "text", "content": "\ud800"}}}'
    response = client.post("/v1/render", content=body, headers={**TOKEN, "Content-Type": "application/json"})

    assert response.status_code == 400
    problem = response.json()
    assert problem["code"] == "invalid_file_data"
    assert problem["context"] == {"key": "x.typ"}


def test_v1_publishes_the_request_origin_for_a_malformed_body():
    response = client.post("/v1/render", json={}, headers=TOKEN)

    assert response.status_code == 400
    problem = response.json()
    assert problem["code"] == "invalid_request"
    assert problem["origin"] == "request"


def test_v1_publishes_the_template_origin_for_an_oversized_source(make_renderer: Callable[..., TypstRenderer]):
    # 1024 is the settings floor (Settings.max_template_source_bytes has ge=1024), so 16 from the
    # brief would fail Settings construction; 1024/2048 is the smallest pair that still triggers it.
    renderer = make_renderer(max_template_source_bytes=1024)
    app.dependency_overrides[get_renderer] = lambda: renderer
    try:
        response = client.post("/v1/render", json={"source": "x" * 2048}, headers=TOKEN)
    finally:
        del app.dependency_overrides[get_renderer]

    assert response.status_code == 413
    problem = response.json()
    assert problem["code"] == "template_source_too_large"
    # The same status as string_too_large below, and only this field says who has to act.
    assert problem["origin"] == "template"


def test_v1_publishes_the_service_origin_for_our_own_failure():
    renderer = AsyncMock(spec=TypstRenderer)
    renderer.render.side_effect = RenderError("Typst did not produce output file")
    app.dependency_overrides[get_renderer] = lambda: renderer
    try:
        response = client.post("/v1/render", json={"source": "hello"}, headers=TOKEN)
    finally:
        del app.dependency_overrides[get_renderer]

    assert response.status_code == 500
    problem = response.json()
    assert problem["code"] == "render_failed"
    assert problem["origin"] == "service"


def test_v1_publishes_the_request_origin_for_a_missing_token():
    response = client.post("/v1/render", json={"source": "hello"})

    assert response.status_code == 403
    assert response.json()["origin"] == "request"
