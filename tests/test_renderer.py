import asyncio
import base64
import errno
import io
import os
import re
import signal
import sys
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from app.core import constants
from app.core.config import Settings
from app.core.constants import INLINE_TEMPLATE_FILENAME
from app.core.errors import (
    InlineTemplateError,
    InvalidFileDataError,
    OutputTooLargeError,
    PageSelectionTooLargeError,
    RenderError,
    RenderTimeoutError,
    StringTooLargeError,
    TemplateFileTooLargeError,
    TemplateSourceTooLargeError,
    TooManyOutputFilesError,
)
from app.models import (
    OUTPUT_MODELS,
    JSONValue,
    OutputFormat,
    PdfOutput,
    PngOutput,
    RenderFile,
    RenderJob,
    RenderOutput,
    RenderRequest,
    SvgOutput,
)
from app.render.renderer import TypstRenderer, _OutputPlan, _ProjectLayout, resource_limit_args
from tests.conftest import FakeProc, FakeTypst

_OMITTED = object()


def _job(
    source: str = '#text("hi")',
    *,
    files: Mapping[str, RenderFile] | None = None,
    data: JSONValue | object = _OMITTED,
    output_format: OutputFormat = OutputFormat.pdf,
    filename: str | None = None,
    output: RenderOutput | None = None,
) -> RenderJob:
    output_type = {
        OutputFormat.pdf: PdfOutput,
        OutputFormat.png: PngOutput,
        OutputFormat.svg: SvgOutput,
    }[output_format]
    return RenderJob(
        source=source,
        files=files or {},
        data={} if data is _OMITTED else data,
        output=output or output_type(filename=filename),
    )


def _layout(tmp_path: Path) -> _ProjectLayout:
    return _ProjectLayout(
        temp_dir=tmp_path,
        project_root=tmp_path,
        bound_template=tmp_path / "template.typ",
    )


def _plan(tmp_path: Path, output: RenderOutput | None = None) -> _OutputPlan:
    """The compile plan a render would have built, for the tests that drive _run_typst directly."""
    return TypstRenderer(Settings())._output_plan(_layout(tmp_path), output or PdfOutput())


@pytest.fixture
def renderer(make_renderer: Callable[..., TypstRenderer]) -> TypstRenderer:
    """A default-configured renderer, for the tests that need no settings overrides."""
    return make_renderer()


def test_escape_string_preserves_hashes_inside_typst_strings(renderer: TypstRenderer):
    assert renderer._escape_string('text "value"\n#code') == 'text \\"value\\"\\n#code'


def test_escape_string_validates_max_size(renderer: TypstRenderer):
    with pytest.raises(StringTooLargeError):
        renderer._escape_string("x" * (1024 * 1024 + 1))


def test_escape_string_handles_unsafe_unicode(renderer: TypstRenderer):
    assert renderer._escape_string("a\x00\u202eb") == r"a\u{0000}b"


def test_every_output_format_is_claimed_by_exactly_one_model_that_describes_itself():
    """
    The invariant OUTPUT_MODELS's own comment asserts, now that it is the only format registry.

    A format missing from it is one the renderer cannot plan an output for; one whose model omits
    an extension or a media type would reach the response with neither.
    """
    assert set(OUTPUT_MODELS) == set(OutputFormat)
    for output_format, model in OUTPUT_MODELS.items():
        assert model.extension, output_format
        assert model.content_type, output_format
    assert len({model.extension for model in OUTPUT_MODELS.values()}) == len(OUTPUT_MODELS)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "none"),
        (True, "true"),
        (False, "false"),
        (42, "42"),
        ("hello", '"hello"'),
        ([], "()"),
        ([1, "x"], '(1, "x",)'),
        ({}, "(:)"),
        ({"name": "Ada"}, '("name": "Ada")'),
    ],
)
def test_json_to_typst_preserves_json_shapes(renderer: TypstRenderer, value: JSONValue, expected: str):
    assert renderer._json_to_typst(value) == expected


def test_string_too_large_path_names_the_nested_location(make_renderer: Callable[..., TypstRenderer]):
    renderer = make_renderer(max_string_bytes=1024)
    data: JSONValue = {"customer": [{"notes": "x" * 1025}]}
    with pytest.raises(StringTooLargeError) as exc_info:
        renderer._json_to_typst(data)
    assert exc_info.value.context == {
        "path": ["customer", 0, "notes"],
        "subject": "value",
        "size": 1025,
        "limit": 1024,
    }


def test_string_too_large_path_ends_in_an_oversized_key(make_renderer: Callable[..., TypstRenderer]):
    """The offending value is the key itself, so the path names it as its own last segment."""
    renderer = make_renderer(max_string_bytes=1024)
    with pytest.raises(StringTooLargeError) as exc_info:
        renderer._json_to_typst({"outer": {"k" * 1025: "fine"}})
    assert exc_info.value.path == ["outer", "k" * 80 + "…"]


def test_an_oversized_key_and_an_oversized_value_under_it_are_told_apart(
    make_renderer: Callable[..., TypstRenderer],
):
    """
    Both unwind to the same path, the key being its own last segment.

    Without a second field the caller cannot tell which string to shorten: it reads the path, looks
    at the value stored under that key, and finds nothing wrong with it.
    """
    renderer = make_renderer(max_string_bytes=1024)

    with pytest.raises(StringTooLargeError) as oversized_key:
        renderer._json_to_typst({"outer": {"k" * 1025: "fine"}})
    with pytest.raises(StringTooLargeError) as oversized_value:
        renderer._json_to_typst({"outer": {"k": "v" * 1025}})

    assert oversized_key.value.context["subject"] == "key"
    assert oversized_value.value.context["subject"] == "value"


def test_string_too_large_path_segments_are_bounded(make_renderer: Callable[..., TypstRenderer]):
    """Object keys in data are unvalidated caller text, so the path shortens them like prose does."""
    renderer = make_renderer(max_string_bytes=1024)
    long_key = "a" * 200
    with pytest.raises(StringTooLargeError) as exc_info:
        renderer._json_to_typst({long_key: "x" * 1025})
    (segment,) = exc_info.value.path
    assert isinstance(segment, str)
    assert segment == "a" * 80 + "…"


def test_string_too_large_from_a_bare_string_has_an_empty_path(make_renderer: Callable[..., TypstRenderer]):
    renderer = make_renderer(max_string_bytes=1024)
    with pytest.raises(StringTooLargeError) as exc_info:
        renderer._json_to_typst("x" * 1025)
    assert exc_info.value.path == []


@pytest.mark.parametrize("data", [None, True, 123, "text", ["a", 2], {"name": "Ada"}])
@pytest.mark.asyncio
async def test_render_binds_data_without_wrapping_or_merging(
    data: JSONValue,
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    typst = fake_typst()

    await renderer.render(_job(data=data))

    typst_value = renderer._json_to_typst(data)
    assert typst.call.source.startswith(f"#let request = {typst_value};\n#let data = request;\n")


@pytest.mark.asyncio
async def test_data_image_string_is_bound_verbatim_and_creates_no_implicit_file(
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    data_image = "data:image/png;base64,aGVsbG8="
    typst = fake_typst()

    await renderer.render(_job(data={"logo": data_image}))

    assert list(typst.call.files) == [INLINE_TEMPLATE_FILENAME]
    assert data_image in typst.call.source


@pytest.mark.asyncio
async def test_render_strips_unencodable_surrogates_from_data(
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    typst = fake_typst()

    await renderer.render(_job(data={"name": "a\ud800b"}))

    assert '("name": "ab")' in typst.call.source


@pytest.mark.asyncio
async def test_render_writes_text_and_binary_files(
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    binary = b"\x89PNG\r\n\x1a\nfake"
    typst = fake_typst()

    files = {
        "lib/x.typ": RenderFile(encoding="text", content="#let x = 1"),
        "assets/logo.png": RenderFile(encoding="base64", content=base64.b64encode(binary).decode()),
    }
    await renderer.render(_job(files=files))

    assert typst.call.files["lib/x.typ"] == b"#let x = 1"
    assert typst.call.files["assets/logo.png"] == binary


@pytest.mark.asyncio
async def test_render_rejects_invalid_base64(make_renderer: Callable[..., TypstRenderer]):
    renderer = make_renderer()
    files = {"asset.png": RenderFile(encoding="base64", content="not!base64")}
    with pytest.raises(InvalidFileDataError):
        await renderer.render(_job(files=files))


@pytest.mark.asyncio
async def test_render_rejects_oversized_text_file(make_renderer: Callable[..., TypstRenderer]):
    renderer = make_renderer(max_inline_file_bytes=1024)
    files = {"lib/big.typ": RenderFile(encoding="text", content="x" * 1025)}
    with pytest.raises(TemplateFileTooLargeError, match=re.escape("lib/big.typ")) as exc_info:
        await renderer.render(_job(files=files))
    assert exc_info.value.context == {"key": "lib/big.typ", "size": 1025, "limit": 1024}


@pytest.mark.asyncio
async def test_render_carries_a_long_file_key_whole_in_context(make_renderer: Callable[..., TypstRenderer]):
    """The prose shortens the key; the context does not, so a caller can match it against its own map."""
    renderer = make_renderer(max_inline_file_bytes=1024)
    key = "assets/" + "a" * 100 + ".png"
    files = {key: RenderFile(encoding="text", content="x" * 1025)}
    with pytest.raises(TemplateFileTooLargeError) as exc_info:
        await renderer.render(_job(files=files))
    assert exc_info.value.context["key"] == key
    assert key not in exc_info.value.detail
    assert "…" in exc_info.value.detail


@pytest.mark.asyncio
async def test_render_rejects_oversized_base64_before_decoding(
    monkeypatch: pytest.MonkeyPatch,
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer(max_inline_file_bytes=1024)

    def fail_on_decode(*_args, **_kwargs):
        raise AssertionError("b64decode must not run for an entry rejected on encoded length")

    monkeypatch.setattr(base64, "b64decode", fail_on_decode)
    files = {"asset.png": RenderFile(encoding="base64", content="A" * 100_000)}
    with pytest.raises(TemplateFileTooLargeError):
        await renderer.render(_job(files=files))


@pytest.mark.asyncio
async def test_render_accepts_base64_at_exactly_the_limit(
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer(max_inline_file_bytes=1024)
    typst = fake_typst()

    encoded = base64.b64encode(b"A" * 1024).decode()
    await renderer.render(_job(files={"asset.bin": RenderFile(encoding="base64", content=encoded)}))
    assert len(typst.call.files["asset.bin"]) == 1024


@pytest.mark.asyncio
async def test_render_propagates_disk_failures(
    monkeypatch: pytest.MonkeyPatch,
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()

    def out_of_space(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", out_of_space)
    files = {"lib/x.typ": RenderFile(encoding="text", content="x")}
    with pytest.raises(OSError, match="No space left on device") as exc_info:
        await renderer.render(_job(files=files))
    assert exc_info.value.errno == errno.ENOSPC


@pytest.mark.asyncio
async def test_render_rejects_unencodable_source_and_file(make_renderer: Callable[..., TypstRenderer]):
    renderer = make_renderer()
    with pytest.raises(InvalidFileDataError):
        await renderer.render(_job(source='#text("\ud800")'))
    with pytest.raises(InvalidFileDataError):
        await renderer.render(_job(files={"x.typ": RenderFile(encoding="text", content="\ud800")}))


@pytest.mark.asyncio
async def test_render_rejects_oversized_source(make_renderer: Callable[..., TypstRenderer]):
    renderer = make_renderer(max_template_source_bytes=1024)
    with pytest.raises(TemplateSourceTooLargeError) as exc_info:
        await renderer.render(_job(source="x" * 1025))
    assert exc_info.value.context == {"size": 1025, "limit": 1024}


@pytest.mark.asyncio
async def test_render_minimal_job_and_output_filename(
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    _ = fake_typst()

    result = await renderer.render(_job(filename="../report.untrusted"))
    assert result.bytes == b"%PDF-fake"
    assert result.content_type == "application/pdf"
    assert result.filename == "report.pdf"


@pytest.mark.asyncio
async def test_returned_filename_is_bounded(
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    _ = fake_typst()

    result = await renderer.render(_job(filename="A" * 5000 + ".pdf"))
    assert len(result.filename) <= 255
    assert result.filename.endswith(".pdf")


@pytest.mark.asyncio
async def test_oversized_output_is_rejected_before_it_is_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer(max_output_bytes=1024)
    plan = _plan(tmp_path)
    plan.typst_output.write_bytes(b"x" * 1025)

    def fail_on_read(_path: Path) -> bytes:
        raise AssertionError("oversized output was read into memory")

    monkeypatch.setattr(Path, "read_bytes", fail_on_read)

    with pytest.raises(OutputTooLargeError, match="1025"):
        await renderer._finalise_output(plan)


@pytest.mark.asyncio
async def test_the_temporary_project_is_removed_after_a_successful_render(
    fake_typst: Callable[..., FakeTypst],
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
):
    """Cleanup is explicit code now, not TemporaryDirectory's __exit__, so it is pinned here."""
    renderer = make_renderer(temp_root=tmp_path)
    _ = fake_typst()

    _ = await renderer.render(_job(files={"lib/x.typ": RenderFile(encoding="text", content="#let x = 1")}))

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_the_temporary_project_is_removed_when_the_render_fails(
    fake_typst: Callable[..., FakeTypst],
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
):
    """A compile failure must not leak the project tree: the tree is where caller source lives."""
    renderer = make_renderer(temp_root=tmp_path)
    _ = fake_typst(content=None)

    with pytest.raises(RenderError, match="did not produce output"):
        _ = await renderer.render(_job())

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_debug_copies_are_bounded_and_unique(tmp_path: Path, make_renderer: Callable[..., TypstRenderer]):
    debug_dir = tmp_path / "debug"
    renderer = make_renderer(debug_output_dir=debug_dir)

    for index in range(3):
        output = tmp_path / f"output-{index}.pdf"
        output.write_bytes(f"%PDF-{index}".encode())
        await renderer._save_debug_copy(output, "B" * 5000 + ".pdf")

    copies = list(debug_dir.iterdir())
    assert len(copies) == 3
    assert all(copy.name.startswith("render-") and len(copy.name) <= 255 for copy in copies)
    assert {copy.read_bytes() for copy in copies} == {b"%PDF-0", b"%PDF-1", b"%PDF-2"}


@pytest.mark.asyncio
async def test_run_typst_kills_process_on_cancel(
    fake_typst: Callable[..., FakeTypst],
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    proc = FakeProc(blocks_for=1)
    _ = fake_typst(proc=proc, content=None)

    task = asyncio.create_task(renderer._run_typst(_layout(tmp_path), _plan(tmp_path)))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed is True


@pytest.mark.asyncio
async def test_run_typst_times_out(
    fake_typst: Callable[..., FakeTypst],
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer(render_timeout_secs=1)
    _ = fake_typst(proc=FakeProc(blocks_for=2), content=None)

    with pytest.raises(RenderTimeoutError):
        await renderer._run_typst(_layout(tmp_path), _plan(tmp_path))


@pytest.mark.asyncio
async def test_run_typst_classifies_compile_failure_as_caller_error(
    fake_typst: Callable[..., FakeTypst],
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    typst = fake_typst(proc=FakeProc(returncode=1), content=None)

    with pytest.raises(InlineTemplateError) as exc_info:
        await renderer._run_typst(_layout(tmp_path), _plan(tmp_path))
    assert exc_info.value.status < 500
    assert typst.call.kwargs["stdout"] is asyncio.subprocess.DEVNULL
    assert typst.call.kwargs["stderr"] is asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_run_typst_requires_an_output(
    fake_typst: Callable[..., FakeTypst],
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    _ = fake_typst(content=None)

    with pytest.raises(RenderError, match="did not produce output"):
        await renderer._run_typst(_layout(tmp_path), _plan(tmp_path))


def test_typst_env_blocks_proxies_by_default(renderer: TypstRenderer):
    env = renderer._typst_env()
    assert env is not None
    for variable in ("http_proxy", "https_proxy", "all_proxy"):
        assert env[variable] == env[variable.upper()] != ""
    assert env["NO_PROXY"] == env["no_proxy"] == ""


def test_typst_env_allows_proxies_through_when_network_is_enabled(
    make_renderer: Callable[..., TypstRenderer],
    monkeypatch: pytest.MonkeyPatch,
):
    """An operator who enables package downloads is often behind a proxy; their setting must survive."""
    monkeypatch.setenv("https_proxy", "http://proxy.internal:3128")

    env = make_renderer(allow_typst_network=True)._typst_env()

    assert env["https_proxy"] == "http://proxy.internal:3128"


@pytest.mark.parametrize("allow_network", [False, True])
def test_typst_env_withholds_service_secrets_from_the_compiler(
    make_renderer: Callable[..., TypstRenderer],
    monkeypatch: pytest.MonkeyPatch,
    allow_network: bool,
):
    """The compiler executes caller source, so the process environment is not its to read.

    Typst exposes no way to read environment variables today, which makes this defence in depth
    rather than a fix: a future language feature, or a plugin gaining host access, would otherwise
    turn every render into a credential disclosure.
    """
    monkeypatch.setenv("PRELUM_API_TOKEN", "the-real-token")
    monkeypatch.setenv("PRELUM_SENTRY_DSN", "https://key@sentry.invalid/1")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unrelated-but-present")

    env = make_renderer(allow_typst_network=allow_network)._typst_env()

    assert "PRELUM_API_TOKEN" not in env
    assert "PRELUM_SENTRY_DSN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "the-real-token" not in "".join(env.values())


def test_typst_env_withholds_typst_variables_that_would_override_the_arguments(
    make_renderer: Callable[..., TypstRenderer],
    monkeypatch: pytest.MonkeyPatch,
):
    """TYPST_ROOT and its siblings are argument fallbacks; inheriting them would make confinement ambient."""
    monkeypatch.setenv("TYPST_ROOT", "/")
    monkeypatch.setenv("TYPST_FONT_PATHS", "/etc")
    monkeypatch.setenv("TYPST_PACKAGE_PATH", "/etc")

    env = make_renderer()._typst_env()

    assert not [name for name in env if name.startswith("TYPST_")]


def test_typst_env_keeps_path_so_a_bare_cli_path_still_resolves(renderer: TypstRenderer):
    """`cli_path` defaults to the bare name 'typst', which execvp can only find through PATH."""
    assert renderer._typst_env()["PATH"] == os.environ["PATH"]


@pytest.mark.asyncio
async def test_run_typst_passes_root_font_and_package_controls(
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    typst = fake_typst()

    await renderer.render(_job())

    argv, project = typst.call.argv, str(typst.call.project_root)
    assert argv[argv.index("--root") + 1] == project
    assert argv[argv.index("--font-path") + 1] == project
    assert "--package-path" in argv
    assert "--package-cache-path" in argv
    assert isinstance(typst.call.kwargs["env"], dict)


@pytest.mark.parametrize(
    ("output_data", "expected_args"),
    [
        (
            {"format": "pdf", "version": "1.7", "standards": ["a-2b"], "pages": "1,3-4"},
            ("--pdf-standard", "1.7,a-2b", "--pages", "1,3-4"),
        ),
        ({"format": "png", "ppi": 300, "page": 2}, ("--ppi", "300", "--pages", "2")),
        ({"format": "svg", "page": 2}, ("--pages", "2")),
    ],
)
@pytest.mark.asyncio
async def test_run_typst_passes_format_specific_output_options(
    output_data: dict[str, object],
    expected_args: tuple[str, ...],
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    typst = fake_typst(content=b"output")
    output = RenderRequest.model_validate({"source": '#text("hi")', "output": output_data}).output

    await renderer.render(_job(output=output))

    argv = typst.call.argv
    option_start = argv.index(expected_args[0])
    assert argv[option_start : option_start + len(expected_args)] == expected_args


@pytest.mark.asyncio
async def test_run_typst_adds_configured_read_only_resource_paths(
    tmp_path: Path,
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    fonts = tmp_path / "fonts"
    packages = tmp_path / "packages"
    fonts.mkdir()
    packages.mkdir()
    renderer = make_renderer(font_path=fonts, local_package_path=packages)
    typst = fake_typst()

    await renderer.render(_job())

    argv, project = typst.call.argv, str(typst.call.project_root)
    assert argv[argv.index("--font-path") + 1] == os.pathsep.join((project, str(fonts)))
    assert argv[argv.index("--package-path") + 1] == str(packages)
    assert argv[argv.index("--package-cache-path") + 1] != str(packages)


@pytest.mark.asyncio
async def test_image_archive_uses_one_bounded_typst_invocation(
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer(max_output_files=3)
    typst = fake_typst(pages={1: b"page one", 3: b"page three"})
    output = PngOutput(archive="zip", pages="1,3-", ppi=72)

    result = await renderer.render(_job(output=output))

    argv = typst.call.argv
    assert typst.call.output.name.endswith("output-{p}.png")
    assert argv[argv.index("--pages") + 1] == "1,3,4,5"
    assert argv[argv.index("--ppi") + 1] == "72"
    assert result.content_type == "application/zip"
    assert result.filename == "rendered.zip"
    assert result.disposition == "attachment"


@pytest.mark.asyncio
async def test_image_archive_orders_and_safely_names_physical_pages(
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    _ = fake_typst(pages={10: b"ten", 2: b"two"})

    result = await renderer.render(_job(output=SvgOutput(archive="zip", pages="2,10", filename="../selected.svg")))

    with zipfile.ZipFile(io.BytesIO(result.bytes)) as archive:
        assert archive.namelist() == ["page-02.svg", "page-10.svg"]
        assert archive.read("page-02.svg") == b"two"
        assert archive.read("page-10.svg") == b"ten"
        assert {entry.date_time for entry in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}
        assert {entry.external_attr >> 16 for entry in archive.infolist()} == {0o100644}
    assert result.filename == "selected.zip"


@pytest.mark.asyncio
async def test_image_archive_rejects_the_output_file_sentinel(
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer(max_output_files=2)
    _ = fake_typst(pages=dict.fromkeys(range(1, 4), b"page"))

    with pytest.raises(TooManyOutputFilesError) as exc_info:
        await renderer.render(_job(output=SvgOutput(archive="zip")))

    assert exc_info.value.status == 413
    assert exc_info.value.code == "too_many_output_files"
    # Typst rendered limit + 1 pages by design; that constant is not a page count and is not reported.
    assert exc_info.value.context == {"limit": renderer.settings.max_output_files}


@pytest.mark.asyncio
async def test_page_selection_beyond_the_limit_fails_before_rendering(
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer(max_output_files=2)
    typst = fake_typst()

    with pytest.raises(PageSelectionTooLargeError) as exc_info:
        await renderer.render(_job(output=PngOutput(archive="zip", pages="1-3")))

    assert exc_info.value.code == "page_selection_too_large"
    assert exc_info.value.context == {"count": 3, "limit": 2}
    assert typst.calls == [], "no render may be spent on a selection that cannot fit the archive"


@pytest.mark.asyncio
async def test_image_archive_rejects_aggregate_output_before_reading_files(
    fake_typst: Callable[..., FakeTypst],
    monkeypatch: pytest.MonkeyPatch,
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer(max_output_bytes=1024)
    _ = fake_typst(pages={1: b"a" * 600, 2: b"b" * 425})

    def fail_on_read(_path: Path) -> bytes:
        raise AssertionError("oversized page files must not be read into memory")

    monkeypatch.setattr(Path, "read_bytes", fail_on_read)

    with pytest.raises(OutputTooLargeError, match="1025"):
        await renderer.render(_job(output=PngOutput(archive="zip", pages="1-2")))


def test_output_size_accepts_the_exact_configured_boundary(make_renderer: Callable[..., TypstRenderer]):
    renderer = make_renderer(max_output_bytes=1024)

    renderer._check_output_size(1024)


@pytest.mark.asyncio
async def test_image_archive_rejects_an_explicit_selection_matching_no_page(
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    _ = fake_typst(content=None)

    with pytest.raises(InlineTemplateError):
        await renderer.render(_job(output=SvgOutput(archive="zip", pages="99")))


@pytest.mark.asyncio
async def test_image_archive_treats_unexpected_empty_all_page_output_as_a_server_fault(
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    _ = fake_typst(content=None)

    with pytest.raises(RenderError, match="did not produce output"):
        await renderer.render(_job(output=SvgOutput(archive="zip")))


def test_image_archive_rejects_final_zip_bytes_over_the_output_limit(
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer(max_output_files=20, max_output_bytes=1024)
    output_template = tmp_path / "output-{p}.svg"
    for page in range(1, 12):
        (tmp_path / f"output-{page}.svg").write_bytes(b"")

    with pytest.raises(OutputTooLargeError):
        renderer._create_archive(renderer._collect_page_outputs(output_template), tmp_path / "output.zip", "svg")


def test_image_archive_requires_at_least_one_page_file(
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()

    with pytest.raises(RenderError, match="did not produce page output"):
        renderer._create_archive([], tmp_path / "output.zip", "svg")


def test_image_archive_rejects_a_matching_symlink(
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    target = tmp_path / "target.svg"
    target.write_bytes(b"svg")
    (tmp_path / "output-1.svg").symlink_to(target)

    with pytest.raises(RenderError, match="invalid page output"):
        renderer._collect_page_outputs(tmp_path / "output-{p}.svg")


@pytest.mark.asyncio
async def test_image_archive_debug_copy_contains_only_the_completed_archive(
    tmp_path: Path,
    fake_typst: Callable[..., FakeTypst],
    make_renderer: Callable[..., TypstRenderer],
):
    debug_dir = tmp_path / "debug"
    renderer = make_renderer(debug_output_dir=debug_dir)
    _ = fake_typst(pages={1: b"svg"})

    result = await renderer.render(_job(output=SvgOutput(archive="zip", pages="1")))

    copies = list(debug_dir.iterdir())
    assert len(copies) == 1
    assert copies[0].suffix == ".zip"
    assert copies[0].read_bytes() == result.bytes


@pytest.mark.asyncio
async def test_explicit_missing_image_page_is_a_caller_compile_failure(
    fake_typst: Callable[..., FakeTypst],
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    _ = fake_typst(content=None)

    with pytest.raises(InlineTemplateError):
        await renderer._run_typst(_layout(tmp_path), _plan(tmp_path, SvgOutput(page=99)))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_render_full_pipeline_produces_pdf(make_renderer: Callable[..., TypstRenderer]):
    renderer = make_renderer(cli_path=Path("typst"))
    result = await renderer.render(_job(source='#text("Hello " + data.name)', data={"name": "World"}))
    assert result.content_type == "application/pdf"
    assert result.bytes.startswith(b"%PDF")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_render_preserves_hashes_in_data_strings(make_renderer: Callable[..., TypstRenderer]):
    renderer = make_renderer(cli_path=Path("typst"))
    source = '#assert(data.comment == "# a comment")\n#text(data.comment)'

    result = await renderer.render(_job(source=source, data={"comment": "# a comment"}))

    assert result.bytes.startswith(b"%PDF")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_render_with_auxiliary_text_and_binary_files(make_renderer: Callable[..., TypstRenderer]):
    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
    renderer = make_renderer(cli_path=Path("typst"))
    files = {
        "lib/greeting.typ": RenderFile(encoding="text", content="#let greet(name) = [Hello, #name!]"),
        "assets/logo.png": RenderFile(encoding="base64", content=png),
    }
    source = '#import "lib/greeting.typ": greet\n#greet(data.name)\n#image("assets/logo.png", width: 10pt)'
    result = await renderer.render(_job(source=source, files=files, data={"name": "World"}))
    assert result.bytes.startswith(b"%PDF")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_render_uses_a_nested_request_supplied_font(make_renderer: Callable[..., TypstRenderer]):
    renderer = make_renderer(cli_path=Path("typst"))
    font_bytes = (Path(__file__).parent / "fixtures" / "fonts" / "Tiny5-Regular.ttf").read_bytes()
    files = {
        "fonts/custom/Tiny5-Regular.ttf": RenderFile(
            encoding="base64",
            content=base64.b64encode(font_bytes).decode("ascii"),
        )
    }
    source = '#set text(font: "Tiny5", size: 24pt)\nRequest supplied font'

    fallback = await renderer.render(_job(source=source, output_format=OutputFormat.svg))
    with_font = await renderer.render(_job(source=source, files=files, output_format=OutputFormat.svg))

    assert with_font.content_type == "image/svg+xml"
    assert with_font.bytes.startswith(b"<svg ")
    assert with_font.bytes != fallback.bytes


@pytest.mark.integration
@pytest.mark.asyncio
async def test_render_uses_a_configured_font_directory(make_renderer: Callable[..., TypstRenderer]):
    font_path = (Path(__file__).parent / "fixtures" / "fonts").resolve()
    source = '#set text(font: "Tiny5", size: 24pt)\nMounted font'

    fallback = await make_renderer(cli_path=Path("typst")).render(_job(source=source, output_format=OutputFormat.svg))
    mounted = await make_renderer(cli_path=Path("typst"), font_path=font_path).render(
        _job(source=source, output_format=OutputFormat.svg)
    )

    assert mounted.bytes.startswith(b"<svg ")
    assert mounted.bytes != fallback.bytes


@pytest.mark.integration
@pytest.mark.asyncio
async def test_render_imports_a_configured_local_package(make_renderer: Callable[..., TypstRenderer]):
    package_path = (Path(__file__).parent / "fixtures" / "packages").resolve()
    renderer = make_renderer(cli_path=Path("typst"), local_package_path=package_path)
    source = '#import "@local/prelum-test:1.0.0": package-message\n#package-message("hello")'

    result = await renderer.render(_job(source=source))

    assert result.bytes.startswith(b"%PDF")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_image_archive_enforces_the_configured_file_limit_with_real_typst(
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer(cli_path=Path("typst"), max_output_files=2)
    source = '#text("first")\n#pagebreak()\n#text("second")\n#pagebreak()\n#text("third")'

    with pytest.raises(TooManyOutputFilesError):
        await renderer.render(_job(source=source, output=SvgOutput(archive="zip")))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_typst_blocks_remote_and_local_packages(make_renderer: Callable[..., TypstRenderer]):
    renderer = make_renderer(cli_path=Path("typst"))
    for source in (
        '#import "@preview/tidy:0.4.0"\n#text("hi")',
        '#import "@local/anything:1.0.0"\n#text("hi")',
    ):
        with pytest.raises(InlineTemplateError):
            await renderer.render(_job(source=source))


@pytest.mark.asyncio
async def test_invalid_base64_reports_the_key(make_renderer: Callable[..., TypstRenderer]):
    renderer = make_renderer()
    files = {"asset.png": RenderFile(encoding="base64", content="not!base64")}
    with pytest.raises(InvalidFileDataError) as exc_info:
        await renderer.render(_job(files=files))
    assert exc_info.value.context == {"key": "asset.png"}


@pytest.mark.asyncio
async def test_unencodable_file_reports_the_key_and_unencodable_source_reports_nothing(
    make_renderer: Callable[..., TypstRenderer],
):
    renderer = make_renderer()
    with pytest.raises(InvalidFileDataError) as file_info:
        await renderer.render(_job(files={"x.typ": RenderFile(encoding="text", content="\ud800")}))
    assert file_info.value.context == {"key": "x.typ"}

    with pytest.raises(InvalidFileDataError) as source_info:
        await renderer.render(_job(source="\ud800"))
    assert source_info.value.context == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("signal_number", [signal.SIGSEGV, signal.SIGBUS, signal.SIGILL])
async def test_run_typst_classifies_a_crashing_signal_as_infrastructure(
    fake_typst: Callable[..., FakeTypst],
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
    signal_number: signal.Signals,
):
    """Typst dying on SIGSEGV is Typst failing, not the caller's source being wrong."""
    renderer = make_renderer()
    _ = fake_typst(proc=FakeProc(returncode=-signal_number), content=None)

    with pytest.raises(RenderError) as exc_info:
        await renderer._run_typst(_layout(tmp_path), _plan(tmp_path))
    assert exc_info.value.is_server_fault


_BOUNDED = 512 * 1024 * 1024


async def _run_under_signal(
    monkeypatch: pytest.MonkeyPatch,
    fake_typst: Callable[..., FakeTypst],
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
    limit: int | None,
    signal_number: signal.Signals,
) -> None:
    if limit is not None:
        _ = _with_prlimit(monkeypatch)
    renderer = make_renderer(max_render_memory_bytes=limit)
    _ = fake_typst(proc=FakeProc(returncode=-signal_number), content=None)

    await renderer._run_typst(_layout(tmp_path), _plan(tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit", "signal_number"),
    [(None, signal.SIGKILL), (_BOUNDED, signal.SIGABRT)],
    ids=["unbounded-sigkill", "bounded-sigabrt"],
)
async def test_the_signal_a_render_s_own_memory_bound_produces_is_the_callers_fault(
    monkeypatch: pytest.MonkeyPatch,
    fake_typst: Callable[..., FakeTypst],
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
    limit: int | None,
    signal_number: signal.Signals,
):
    """
    Which signal means "this template wanted too much memory" depends on whether a bound is set.

    Unbounded, the kernel's OOM killer sends SIGKILL. Bounded by RLIMIT_DATA, the allocation fails
    inside Typst and Rust aborts, which is SIGABRT. A caller provokes whichever applies on demand,
    so paging on-call for it would hand every caller a lever on the alert channel.
    """
    with pytest.raises(InlineTemplateError) as exc_info:
        await _run_under_signal(monkeypatch, fake_typst, tmp_path, make_renderer, limit, signal_number)
    assert not exc_info.value.is_server_fault


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit", "signal_number"),
    [(_BOUNDED, signal.SIGKILL), (None, signal.SIGABRT)],
    ids=["bounded-sigkill", "unbounded-sigabrt"],
)
async def test_a_signal_the_bound_does_not_explain_is_infrastructure(
    monkeypatch: pytest.MonkeyPatch,
    fake_typst: Callable[..., FakeTypst],
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
    limit: int | None,
    signal_number: signal.Signals,
):
    """
    The mirror image, and the reason the rule is conditional rather than a fixed set of signals.

    A bounded render that exhausts its own limit aborts, so a SIGKILL can only have come from
    outside it — the pod's memory limit, an eviction, a supervisor — every one of which is an
    infrastructure fault that must stay visible. Unbounded, SIGABRT is Typst aborting on its own.
    """
    with pytest.raises(RenderError) as exc_info:
        await _run_under_signal(monkeypatch, fake_typst, tmp_path, make_renderer, limit, signal_number)
    assert exc_info.value.is_server_fault


def _with_prlimit(monkeypatch: pytest.MonkeyPatch, path: str = "/bin/sh") -> str:
    """
    Point both bindings of PRLIMIT_PATH at a real executable.

    Every module reads the constant through app.core.constants rather than importing it by name,
    so one patch covers Settings validation and argv assembly alike. /bin/sh stands in for the
    wrapper, which does not exist on macOS: these tests fake the subprocess and only ever assert on
    argv, so nothing is executed.
    """
    monkeypatch.setattr(constants, "PRLIMIT_PATH", Path(path))
    return path


def test_resource_limit_args_are_empty_when_no_limit_is_configured(
    make_renderer: Callable[..., TypstRenderer],
):
    """The wrapper is Linux-only, so it stays off until a deployment asks for it."""
    assert resource_limit_args(make_renderer().settings) == ()


def test_resource_limit_args_attach_the_value_to_its_flag(
    monkeypatch: pytest.MonkeyPatch,
    make_renderer: Callable[..., TypstRenderer],
):
    """
    prlimit's --data takes an *optional* argument, because it doubles as a query.

    Split as ("--data", "N") getopt does not attach the value: prlimit then reads N as the command
    to exec, leaves the limit unset and exits 127 — which this service would report as a 422
    blaming the caller. Only the joined form works, so the exact token is pinned here.
    """
    wrapper = _with_prlimit(monkeypatch)
    renderer = make_renderer(max_render_memory_bytes=256 * 1024 * 1024)

    assert resource_limit_args(renderer.settings) == (wrapper, "--data=268435456", "--")


@pytest.mark.asyncio
async def test_run_typst_prefixes_the_wrapper_without_disturbing_the_argv_tail(
    monkeypatch: pytest.MonkeyPatch,
    fake_typst: Callable[..., FakeTypst],
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
):
    """Template and output are addressed from the end of argv, so a prefix must not move them."""
    wrapper = _with_prlimit(monkeypatch)
    renderer = make_renderer(max_render_memory_bytes=256 * 1024 * 1024)
    typst = fake_typst()

    plan = _plan(tmp_path)
    await renderer._run_typst(_layout(tmp_path), plan)

    assert typst.call.argv[:3] == (wrapper, "--data=268435456", "--")
    assert typst.call.argv[3] == str(renderer.settings.cli_path)
    assert typst.call.output == plan.typst_output
    assert typst.call.template == _layout(tmp_path).bound_template


@pytest.mark.asyncio
async def test_run_typst_invokes_typst_directly_when_no_limit_is_configured(
    fake_typst: Callable[..., FakeTypst],
    tmp_path: Path,
    make_renderer: Callable[..., TypstRenderer],
):
    """Without this the default-off promise is untested: argv would grow a wrapper unnoticed."""
    renderer = make_renderer()
    typst = fake_typst()

    await renderer._run_typst(_layout(tmp_path), _plan(tmp_path))

    assert typst.call.argv[0] == str(renderer.settings.cli_path)


# The limit these two share. High enough that a legitimate document renders — a 300-section A4
# document was measured to abort at 64 MiB and render at 128 MiB — and low enough that a runaway
# template reaches it quickly.
_INTEGRATION_MEMORY_LIMIT = 256 * 1024 * 1024


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="prlimit is a Linux wrapper")
@pytest.mark.asyncio
async def test_a_bounded_render_still_compiles_with_real_typst(make_renderer: Callable[..., TypstRenderer]):
    """
    The control half of the pair, and the half that catches a wrapper that silently does nothing.

    A split "--data N", a missing "--" or a limit set too low all make the negative test below pass
    for the wrong reason. Only a successful render proves the wrapper was assembled correctly.
    """
    renderer = make_renderer(cli_path=Path("typst"), max_render_memory_bytes=_INTEGRATION_MEMORY_LIMIT)

    result = await renderer.render(_job(source='#text("hello")'))

    assert result.bytes.startswith(b"%PDF")


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="prlimit is a Linux wrapper")
@pytest.mark.asyncio
async def test_a_template_that_allocates_without_bound_is_stopped_by_the_limit():
    """
    A runaway template dies against its own limit rather than the container's OOM killer.

    This cannot tell SIGABRT from Rust's panic exit 101, and does not try to: both are the caller's
    fault and both raise InlineTemplateError. The signal-to-status mapping is pinned by the unit
    tests above, which is why this one asserts only the outcome — do not delete them as redundant.

    The timeout is raised so a slow machine cannot make RenderTimeoutError win the race.
    """
    renderer = TypstRenderer(
        Settings(
            cli_path=Path("typst"),
            max_render_memory_bytes=_INTEGRATION_MEMORY_LIMIT,
            render_timeout_secs=120,
        )
    )
    source = '#let s = "x" * 100\n#let big = range(0, 4000000).map(i => s)\n#big.len()'

    with pytest.raises(InlineTemplateError) as exc_info:
        await renderer.render(_job(source=source))
    assert not exc_info.value.is_server_fault
