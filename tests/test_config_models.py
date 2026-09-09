import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import DEV_DEFAULT_TOKEN, Settings
from app.models import OutputFormat, PngOutput, RenderFile, RenderRequest


def test_settings_defaults():
    settings = Settings()
    assert settings.port == 9870
    assert settings.bind == "0.0.0.0"
    assert settings.render_timeout_secs == 15
    assert settings.max_concurrent_renders == 10


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRELUM_PORT", "8080")
    monkeypatch.setenv("PRELUM_RENDER_TIMEOUT_SECS", "30")
    settings = Settings()
    assert settings.port == 8080
    assert settings.render_timeout_secs == 30


@pytest.mark.parametrize("bind", ["0.0.0.0", "127.0.0.1", "::1", "localhost", "prelum.internal"])
def test_settings_accepts_any_host_uvicorn_accepts(bind: str):
    assert Settings(bind=bind).bind == bind


@pytest.mark.parametrize("bind", ["", "   ", "has space", "tab\there"])
def test_settings_rejects_unbindable_hosts(bind: str):
    with pytest.raises(ValidationError):
        _ = Settings(bind=bind)


def test_output_format_enum():
    assert OutputFormat.pdf == "pdf"
    assert OutputFormat.svg == "svg"
    assert OutputFormat.png == "png"


def test_settings_size_defaults():
    settings = Settings()
    assert settings.max_template_source_bytes == 512 * 1024
    assert settings.max_inline_file_bytes == 1024 * 1024
    assert settings.max_inline_files == 64
    assert settings.max_output_files == 64


def test_settings_size_values_from_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRELUM_MAX_TEMPLATE_SOURCE_BYTES", "1048576")
    monkeypatch.setenv("PRELUM_MAX_INLINE_FILE_BYTES", "4096")
    monkeypatch.setenv("PRELUM_MAX_OUTPUT_FILES", "12")
    settings = Settings()
    assert settings.max_template_source_bytes == 1024 * 1024
    assert settings.max_inline_file_bytes == 4096
    assert settings.max_output_files == 12


def test_resource_paths_default_to_disabled():
    settings = Settings()

    assert settings.font_path is None
    assert settings.local_package_path is None


def test_settings_accepts_existing_absolute_resource_directories(tmp_path: Path):
    fonts = tmp_path / "fonts"
    packages = tmp_path / "packages"
    fonts.mkdir()
    packages.mkdir()

    settings = Settings(font_path=fonts, local_package_path=packages)

    assert settings.font_path == fonts
    assert settings.local_package_path == packages


def test_settings_reads_resource_directories_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fonts = tmp_path / "fonts"
    packages = tmp_path / "packages"
    fonts.mkdir()
    packages.mkdir()
    monkeypatch.setenv("PRELUM_FONT_PATH", str(fonts))
    monkeypatch.setenv("PRELUM_LOCAL_PACKAGE_PATH", str(packages))

    settings = Settings()

    assert settings.font_path == fonts
    assert settings.local_package_path == packages


@pytest.mark.parametrize("field", ["font_path", "local_package_path"])
def test_settings_rejects_a_missing_or_non_directory_resource_path(field: str, tmp_path: Path):
    file_path = tmp_path / "file"
    file_path.write_text("not a directory", encoding="utf-8")

    for invalid_path in (tmp_path / "missing", file_path, Path("relative")):
        with pytest.raises(ValidationError):
            _ = Settings(**{field: invalid_path})


def test_settings_rejects_a_font_directory_containing_the_path_list_separator(tmp_path: Path):
    fonts = tmp_path / f"fonts{os.pathsep}alternate"
    fonts.mkdir()

    with pytest.raises(ValidationError, match="font path must not contain the path separator"):
        _ = Settings(font_path=fonts)


def test_render_request_requires_source():
    with pytest.raises(ValidationError):
        RenderRequest()


def test_render_request_defaults_are_canonical():
    request = RenderRequest(source='#text("hello")')
    assert request.files == {}
    assert request.data == {}
    assert request.output.format == OutputFormat.pdf
    assert request.output.filename is None

    explicit_empty = RenderRequest.model_validate({"source": '#text("hello")', "output": {}})
    assert explicit_empty.output.format == OutputFormat.pdf


def test_pdf_output_accepts_version_standards_and_page_ranges():
    request = RenderRequest.model_validate(
        {
            "source": '#text("hello")',
            "output": {
                "format": "pdf",
                "version": "1.7",
                "standards": ["a-2b"],
                "pages": "1,3-6,8-",
            },
        }
    )

    assert request.output.version == "1.7"
    assert request.output.standards == ["a-2b"]
    assert request.output.pages == "1,3-6,8-"


@pytest.mark.parametrize("output_format", ["png", "svg"])
def test_image_output_accepts_an_explicit_zip_archive(output_format: str):
    request = RenderRequest.model_validate(
        {
            "source": '#text("hello")',
            "output": {
                "format": output_format,
                "archive": "zip",
                "pages": "1,3-6,8-",
                "filename": "pages.zip",
            },
        }
    )

    assert request.output.archive == "zip"
    assert request.output.pages == "1,3-6,8-"
    assert request.output.filename == "pages.zip"


@pytest.mark.parametrize("output_format", ["png", "svg"])
def test_image_zip_archive_defaults_to_all_pages(output_format: str):
    request = RenderRequest.model_validate(
        {"source": '#text("hello")', "output": {"format": output_format, "archive": "zip"}}
    )

    assert request.output.pages is None


@pytest.mark.parametrize(
    "output",
    [
        {"format": "png", "archive": "zip", "page": 1},
        {"format": "svg", "archive": "zip", "page": 1},
        {"format": "png", "pages": "1-2"},
        {"format": "svg", "pages": "1-2"},
        {"format": "pdf", "archive": "zip"},
        {"format": "png", "archive": "tar"},
    ],
)
def test_output_rejects_invalid_archive_combinations(output: dict[str, object]):
    with pytest.raises(ValidationError):
        RenderRequest.model_validate({"source": '#text("hello")', "output": output})


@pytest.mark.parametrize("pages", ["", "0", "1, 2", "3-2", "1,,2", "-3", "1-a"])
def test_image_archive_reuses_pdf_page_selection_syntax(pages: str):
    with pytest.raises(ValidationError):
        RenderRequest.model_validate(
            {
                "source": '#text("hello")',
                "output": {"format": "png", "archive": "zip", "pages": pages},
            }
        )


@pytest.mark.parametrize("pages", ["1\u0662", "1\uff10", "1\U0001d7da"])
@pytest.mark.parametrize(
    "output",
    [
        {"format": "pdf"},
        {"format": "png", "archive": "zip"},
    ],
)
def test_every_page_selection_rejects_unicode_decimal_digits(pages: str, output: dict[str, object]):
    with pytest.raises(ValidationError):
        RenderRequest.model_validate(
            {
                "source": '#text("hello")',
                "output": {**output, "pages": pages},
            }
        )


@pytest.mark.parametrize(
    "output",
    [
        {"format": "pdf", "ppi": 144},
        {"format": "pdf", "page": 1},
        {"format": "png", "standards": ["a-2b"]},
        {"format": "png", "pages": "1-2"},
        {"format": "svg", "ppi": 144},
        {"format": "svg", "version": "1.7"},
    ],
)
def test_output_rejects_options_for_another_format(output: dict[str, object]):
    with pytest.raises(ValidationError):
        RenderRequest.model_validate({"source": '#text("hello")', "output": output})


@pytest.mark.parametrize("ppi", [0, -1, 301, 144.5])
def test_png_output_rejects_unsafe_ppi(ppi: float):
    with pytest.raises(ValidationError):
        RenderRequest.model_validate({"source": '#text("hello")', "output": {"format": "png", "ppi": ppi}})


@pytest.mark.parametrize("ppi", [1, 10, 35])
def test_png_output_accepts_thumbnail_ppi(ppi: int):
    request = RenderRequest.model_validate({"source": '#text("hello")', "output": {"format": "png", "ppi": ppi}})

    assert isinstance(request.output, PngOutput)
    assert request.output.ppi == ppi


@pytest.mark.parametrize("output_format", ["png", "svg"])
@pytest.mark.parametrize("page", [0, -1, 1.5])
def test_image_output_requires_a_positive_integer_page(output_format: str, page: float):
    with pytest.raises(ValidationError):
        RenderRequest.model_validate({"source": '#text("hello")', "output": {"format": output_format, "page": page}})


@pytest.mark.parametrize("pages", ["", "0", "1, 2", "3-2", "1,,2", "-3", "1-a"])
def test_pdf_output_rejects_invalid_page_ranges(pages: str):
    with pytest.raises(ValidationError):
        RenderRequest.model_validate({"source": '#text("hello")', "output": {"format": "pdf", "pages": pages}})


@pytest.mark.parametrize(
    "standards",
    [
        ["a-2b", "a-3b"],
        ["a-2b", "a-2b"],
        ["a-4", "ua-1"],
    ],
)
def test_pdf_output_rejects_incompatible_standards(standards: list[str]):
    with pytest.raises(ValidationError):
        RenderRequest.model_validate({"source": '#text("hello")', "output": {"format": "pdf", "standards": standards}})


def test_pdf_output_accepts_compatible_archive_and_accessibility_standards():
    request = RenderRequest.model_validate(
        {
            "source": '#text("hello")',
            "output": {"format": "pdf", "version": "1.7", "standards": ["a-2b", "ua-1"]},
        }
    )

    assert request.output.standards == ["a-2b", "ua-1"]


@pytest.mark.parametrize(
    "output",
    [
        {"format": "pdf", "version": "1.4", "standards": ["a-2b"]},
        {"format": "pdf", "version": "2.0", "standards": ["ua-1"]},
    ],
)
def test_pdf_output_rejects_a_version_incompatible_with_its_standard(output: dict[str, object]):
    with pytest.raises(ValidationError):
        RenderRequest.model_validate({"source": '#text("hello")', "output": output})


@pytest.mark.parametrize("standard", ["ua-1", "a-1a", "a-2a", "a-3a"])
def test_pdf_page_selection_rejects_tagged_accessibility_standards(standard: str):
    with pytest.raises(ValidationError):
        RenderRequest.model_validate(
            {
                "source": '#text("hello")',
                "output": {"format": "pdf", "standards": [standard], "pages": "1"},
            }
        )


def test_render_request_accepts_all_json_data_shapes():
    for value in (None, True, 123, "text", ["a"], {"name": "Ada"}):
        assert RenderRequest(source="#none", data=value).data == value


def test_render_file_encodings_are_explicit():
    text = RenderFile(content="hello", encoding="text")
    binary = RenderFile(content="aGVsbG8=", encoding="base64")
    assert text.encoding == "text"
    assert binary.encoding == "base64"


@pytest.mark.parametrize(
    "file_data",
    [
        {"content": "hello"},
        {"content": "hello", "encoding": "binary"},
        {"content": "hello", "encoding": "text", "encodings": "base64"},
    ],
)
def test_render_file_rejects_ambiguous_or_unknown_encoding(file_data: dict[str, str]):
    with pytest.raises(ValidationError):
        RenderFile.model_validate(file_data)


def test_render_request_files_validate_keys():
    with pytest.raises(ValidationError):
        RenderRequest(
            source='#text("hi")',
            files={"../escape.typ": RenderFile(encoding="text", content="content")},
        )


def test_render_request_accepts_valid_files():
    request = RenderRequest(
        source='#import "lib/utils.typ"',
        files={"lib/utils.typ": RenderFile(encoding="text", content="#let greet(name) = [Hello #name]")},
    )
    assert request.files["lib/utils.typ"].encoding == "text"


@pytest.mark.parametrize("key", [".", "./", "main.typ", "./main.typ", "main.typ/foo.typ", "plugin.wasm"])
def test_render_request_rejects_invalid_file_keys(key: str):
    with pytest.raises(ValidationError):
        RenderRequest(source='#text("hi")', files={key: RenderFile(encoding="text", content="x")})


def test_render_request_accepts_main_typ_in_subdirectory():
    request = RenderRequest(
        source='#text("hi")',
        files={"lib/main.typ": RenderFile(encoding="text", content="content")},
    )
    assert "lib/main.typ" in request.files


@pytest.mark.parametrize(
    "legacy_field",
    [
        "templateSource",
        "template_source",
        "templateFiles",
        "template_files",
        "templateName",
        "organisationId",
        "format",
        "filename",
        "header",
        "footer",
        "page",
    ],
)
def test_render_request_rejects_legacy_fields(legacy_field: str):
    with pytest.raises(ValidationError):
        RenderRequest.model_validate({"source": '#text("hi")', legacy_field: "legacy"})


def test_render_request_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        RenderRequest(source='#text("hi")', unknown="oops")


def test_render_request_truncates_long_keys_in_error_messages():
    with pytest.raises(ValidationError) as exc_info:
        RenderRequest(
            source='#text("hi")',
            files={"k" * 5000 + "/../x.typ": RenderFile(encoding="text", content="c")},
        )
    assert len(str(exc_info.value)) < 1000


def test_unset_environment_refuses_to_start_without_a_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PRELUM_ENVIRONMENT", raising=False)
    monkeypatch.delenv("PRELUM_API_TOKEN", raising=False)
    with pytest.raises(ValidationError, match="PRELUM_API_TOKEN is required"):
        _ = Settings()


def test_unset_environment_accepts_an_explicit_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PRELUM_ENVIRONMENT", raising=False)
    settings = Settings(api_token="a-real-token")
    assert settings.api_token == "a-real-token"
    assert settings.is_development is False


def test_development_environment_supplies_the_dev_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRELUM_ENVIRONMENT", "development")
    monkeypatch.delenv("PRELUM_API_TOKEN", raising=False)
    assert Settings().api_token == DEV_DEFAULT_TOKEN


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "prod-eu", "live", ""])
def test_non_development_environments_require_a_token(environment: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRELUM_ENVIRONMENT", environment)
    monkeypatch.delenv("PRELUM_API_TOKEN", raising=False)
    with pytest.raises(ValidationError, match="PRELUM_API_TOKEN is required"):
        _ = Settings()


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "", None])
def test_whitespace_only_token_is_refused_outside_development(environment: str | None, monkeypatch: pytest.MonkeyPatch):
    if environment is None:
        monkeypatch.delenv("PRELUM_ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("PRELUM_ENVIRONMENT", environment)
    with pytest.raises(ValidationError, match="PRELUM_API_TOKEN is required"):
        _ = Settings(api_token="   ")


def test_sentry_environment_still_gates_when_environment_is_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PRELUM_ENVIRONMENT", raising=False)
    monkeypatch.setenv("PRELUM_SENTRY_ENVIRONMENT", "development")
    monkeypatch.delenv("PRELUM_API_TOKEN", raising=False)
    assert Settings().api_token == DEV_DEFAULT_TOKEN
