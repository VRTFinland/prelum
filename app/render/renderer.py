import asyncio
import base64
import binascii
import errno
import json
import os
import re
import shutil
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, cast
from uuid import uuid4

import structlog
from structlog.typing import FilteringBoundLogger

from app.core.config import Settings
from app.core.constants import INLINE_TEMPLATE_FILENAME, SAFE_FILENAME_CHARS
from app.core.errors import (
    InlineTemplateError,
    InvalidFileDataError,
    OutputTooLargeError,
    RenderError,
    RenderTimeoutError,
    StringTooLargeError,
    TemplateTooLargeError,
    TooManyOutputFilesError,
    for_message,
    status_is_server_fault,
)
from app.models import (
    JSONValue,
    OutputFormat,
    PageSelectionLimitError,
    PdfOutput,
    PngOutput,
    RenderFile,
    RenderJob,
    RenderOutput,
    SvgOutput,
    bound_page_selection,
)

# Errnos a caller-supplied layout can provoke: the same path claimed as both file and directory, or
# a segment longer than the platform allows. Anything else (ENOSPC, EACCES, EIO) is a server fault.
_LAYOUT_ERRNOS = frozenset({errno.EEXIST, errno.EISDIR, errno.ENOTDIR, errno.ENAMETOOLONG})

# The debug filename is "render-" + 8 hex + "-" + filename, and POSIX NAME_MAX is 255.
_DEBUG_PREFIX = "render"
_DEBUG_UUID_HEX_LENGTH = 8
_MAX_DEBUG_FILENAME_LENGTH = 255 - len(_DEBUG_PREFIX) - 1 - _DEBUG_UUID_HEX_LENGTH - 1

# Bound the caller-controlled Content-Disposition filename; extensions are fixed and short.
_MAX_FILENAME_STEM_LENGTH = 128

# Typst has no offline switch. A closed local proxy rejects package fetches immediately.
_BLACKHOLE_PROXY = "http://127.0.0.1:1"

_PROXY_VARIABLES = tuple(
    variable
    for name in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")
    for variable in (name, name.upper())
)

# Typst compiles correctly with no environment at all, so the subprocess is given only what the
# service itself depends on: PATH to resolve a bare `cli_path`, the TLS trust store and proxy
# settings for package downloads where an operator has enabled them, and SOURCE_DATE_EPOCH, which
# typst reads to make output reproducible. Dropping the rest keeps two classes of variable away
# from caller source: PRELUM_* holds the API token and the Sentry DSN, and TYPST_* would override
# the root, font and package arguments assembled below.
_TYPST_ENV_ALLOWLIST = frozenset({"PATH", "SOURCE_DATE_EPOCH", "SSL_CERT_DIR", "SSL_CERT_FILE", *_PROXY_VARIABLES})

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_ZIP_FILE_MODE = 0o100644


def _build_typst_escapes() -> dict[int, str | None]:
    """
    Map every codepoint Typst cannot take verbatim inside a string literal.

    ``None`` deletes the character. Control characters become ``\\u{....}`` escapes; the direction
    overrides are stripped because they let a string spoof its visual order, and lone surrogates
    because they have no UTF-8 encoding.
    """
    escapes: dict[int, str | None] = {code: f"\\u{{{code:04x}}}" for code in range(0x20)}
    escapes.update({code: f"\\u{{{code:04x}}}" for code in range(0x7F, 0xA0)})
    escapes.update(dict.fromkeys(range(0xD800, 0xE000)))
    escapes.update(dict.fromkeys(range(0x202A, 0x202F)))
    escapes.update(
        {
            ord("\\"): "\\\\",
            ord('"'): '\\"',
            ord("\n"): "\\n",
            ord("\r"): "\\r",
            ord("\t"): "\\t",
            # # allows code interpolation inside strings, so it must be escaped
            ord("#"): "\\#",
        }
    )
    return escapes


# @ is deliberately absent: it is only special for label references in markup mode, and every
# JSON string is emitted inside quotes.
_TYPST_ESCAPES = _build_typst_escapes()


def _decoded_base64_size(content: str) -> int:
    """
    Decoded length of well-formed base64, computed without decoding it.

    Every four encoded characters carry three bytes and padding is excluded, so this is exact for
    any input ``b64decode(validate=True)`` would accept — which lets an oversized payload be
    rejected before it is materialised in memory. Input containing whitespace (which that decode
    rejects outright) measures larger than it decodes to, so such a payload may be reported as too
    large rather than as malformed; it is refused either way, and measuring exactly would cost the
    full-size copy this exists to avoid.
    """
    return len(content.rstrip("=")) * 3 // 4


def _encode_utf8(text: str, subject: str) -> bytes:
    """
    Encode caller-supplied text, reporting an unencodable value as a bad request.

    JSON may carry lone surrogates, which have no UTF-8 representation; letting the
    UnicodeEncodeError escape would turn a malformed request into a 503.
    """
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidFileDataError(f"The {subject} contains characters that cannot be encoded as UTF-8") from exc


logger: FilteringBoundLogger = cast(FilteringBoundLogger, structlog.get_logger())

FORMAT_MAP: dict[OutputFormat, tuple[str, str]] = {
    OutputFormat.pdf: ("pdf", "application/pdf"),
    OutputFormat.svg: ("svg", "image/svg+xml"),
    OutputFormat.png: ("png", "image/png"),
}


@dataclass(frozen=True, slots=True)
class _ProjectLayout:
    """Where one render's files live."""

    temp_dir: Path
    project_root: Path
    bound_template: Path

    @property
    def package_dir(self) -> Path:
        """Empty per-render directory, so '@local/...' cannot resolve from the system data dir."""
        return self.temp_dir / "packages"

    def output_path(self, ext: str) -> Path:
        return self.temp_dir / f"output.{ext}"

    def output_template(self, ext: str) -> Path:
        return self.temp_dir / f"output-{{p}}.{ext}"


@dataclass(frozen=True, slots=True)
class _OutputPlan:
    typst_output: Path
    extension: str
    content_type: str
    response_extension: str
    disposition: Literal["inline", "attachment"]
    page_selection: str | None = None
    archive: bool = False


@dataclass(frozen=True, slots=True)
class RenderResult:
    bytes: bytes
    content_type: str
    filename: str
    disposition: Literal["inline", "attachment"] = "inline"


class TypstRenderer:
    def __init__(self, settings: Settings) -> None:
        self.settings: Settings = settings

    async def render(self, job: RenderJob) -> RenderResult:
        """
        Render a caller-supplied Typst source.

        The temporary project is built from the request alone, so relative imports and asset
        references resolve against the same directory as the compiled entry point::

            project/
            |-- main.typ       prelude (#let request/#let data) + source
            `-- <files keys>   written verbatim; nested keys create their directories

        ``project/`` is also the Typst ``--root``, so nothing outside it is reachable.

        :param job: The validated render job.
        :return: The rendered bytes, content type, and filename.
        :raises TemplateTooLargeError: If the source or an auxiliary file exceeds its size limit.
        :raises InvalidFileDataError: If an auxiliary file is malformed or escapes the project root.
        """
        source_bytes = _encode_utf8(job.source, "source")
        if len(source_bytes) > self.settings.max_template_source_bytes:
            raise TemplateTooLargeError(
                size=len(source_bytes),
                max_allowed=self.settings.max_template_source_bytes,
            )

        logger.info(
            "render.start",
            format=job.output.format.value,
        )

        with TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            project_root = temp_dir / "project"
            project_root.mkdir()

            await asyncio.to_thread(self._write_inline_files, project_root, job.files)

            layout = _ProjectLayout(
                temp_dir=temp_dir,
                project_root=project_root,
                bound_template=project_root / INLINE_TEMPLATE_FILENAME,
            )
            return await self._bind_and_compile(job, layout, source_bytes)

    def _write_inline_files(
        self,
        project_root: Path,
        files: Mapping[str, RenderFile],
    ) -> None:
        """
        Write the caller-supplied auxiliary files into the temporary project.

        Key shape, including conflicts between keys, is settled by ``validate_inline_file_keys`` at
        the model layer. The escape check below asserts that already-proven invariant on a security
        boundary; the errno filter covers what only the filesystem can refuse.

        :param project_root: The temporary project directory, already created.
        :param files: Mapping of relative path to explicitly encoded file content.
        :raises InvalidFileDataError: If there are too many entries, base64 content is malformed, a key
            escapes the project root, two keys differ only in case, or the keys describe a layout
            that cannot be written.
        :raises OSError: If writing fails for a reason outside the caller's control.
        :raises TemplateTooLargeError: If an entry exceeds ``max_inline_file_bytes``.
        """
        if not files:
            return

        limit = self.settings.max_inline_files
        if len(files) > limit:
            raise InvalidFileDataError(f"Too many files entries: {len(files)} exceeds limit {limit}")

        resolved_root = project_root.resolve()
        for relative_key, file_entry in files.items():
            dest = (project_root / relative_key).resolve()
            if not dest.is_relative_to(resolved_root):
                raise InvalidFileDataError(f"File path '{for_message(relative_key)}' escapes project root")
            content_bytes = self._decode_inline_file(relative_key, file_entry)
            try:
                # Nested file keys need their parent directories.
                dest.parent.mkdir(parents=True, exist_ok=True)
                _ = dest.write_bytes(content_bytes)
            except OSError as exc:
                if exc.errno not in _LAYOUT_ERRNOS:
                    raise
                # Two keys can describe a layout no filesystem can hold, e.g. 'lib' as both a file
                # and a directory. That is a malformed request, not a server fault.
                raise InvalidFileDataError(f"File path '{for_message(relative_key)}' cannot be written") from exc

    def _decode_inline_file(self, key: str, entry: RenderFile) -> bytes:
        """
        Resolve one files entry to the bytes to write, enforcing the size limit.

        :param key: The relative path of the entry, used in error messages.
        :param entry: The file's explicit encoding and content.
        :return: The decoded content.
        :raises InvalidFileDataError: If base64 content cannot be decoded.
        :raises TemplateTooLargeError: If the content exceeds ``max_inline_file_bytes``.
        """
        limit = self.settings.max_inline_file_bytes
        if entry.encoding == "base64":
            decoded_size = _decoded_base64_size(entry.content)
            if decoded_size > limit:
                raise TemplateTooLargeError(size=decoded_size, max_allowed=limit, name=for_message(key))
            try:
                return base64.b64decode(entry.content, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise InvalidFileDataError(f"Invalid base64 content for file '{for_message(key)}'") from exc

        content_bytes = _encode_utf8(entry.content, f"content of file '{for_message(key)}'")
        if len(content_bytes) > limit:
            raise TemplateTooLargeError(size=len(content_bytes), max_allowed=limit, name=for_message(key))

        return content_bytes

    async def _bind_and_compile(
        self,
        job: RenderJob,
        layout: _ProjectLayout,
        source_bytes: bytes,
    ) -> RenderResult:
        """
        Bind the request data into the template source, compile it, and finalise the output.

        :param job: The render job, used for data binding and output naming.
        :param layout: The directories this render works in.
        :param source_bytes: The template source to compile, as UTF-8 bytes.
        :return: The rendered bytes, content type, and filename.
        """
        prelude = f"#let request = {self._json_to_typst(job.data)};\n#let data = request;\n"
        bound_bytes = prelude.encode("utf-8") + source_bytes
        _ = await asyncio.to_thread(layout.bound_template.write_bytes, bound_bytes)

        plan = self._output_plan(layout, job.output)
        await self._run_typst(layout, plan.typst_output, job.output, page_selection=plan.page_selection)

        if plan.archive:
            return await self._finalise_archive(layout, plan, job.output)
        return await self._finalise_output(
            plan.typst_output,
            job.output,
            plan.response_extension,
            plan.content_type,
            disposition=plan.disposition,
        )

    def _output_plan(self, layout: _ProjectLayout, output: RenderOutput) -> _OutputPlan:
        ext, content_type = FORMAT_MAP[output.format]
        if isinstance(output, (PngOutput, SvgOutput)) and output.archive == "zip":
            try:
                page_selection = bound_page_selection(output.pages, limit=self.settings.max_output_files)
            except PageSelectionLimitError as exc:
                raise TooManyOutputFilesError(count=exc.count, max_allowed=exc.limit) from exc
            return _OutputPlan(
                typst_output=layout.output_template(ext),
                extension=ext,
                content_type="application/zip",
                response_extension="zip",
                disposition="attachment",
                page_selection=page_selection,
                archive=True,
            )
        return _OutputPlan(
            typst_output=layout.output_path(ext),
            extension=ext,
            content_type=content_type,
            response_extension=ext,
            disposition="inline",
        )

    async def _finalise_output(
        self,
        output_path: Path,
        output: RenderOutput,
        ext: str,
        content_type: str,
        *,
        disposition: Literal["inline", "attachment"] = "inline",
    ) -> RenderResult:
        output_size = (await asyncio.to_thread(output_path.stat)).st_size
        self._check_output_size(output_size)
        output_bytes = await asyncio.to_thread(output_path.read_bytes)
        filename = self._sanitize_filename(
            output.filename or f"rendered.{ext}",
            ext=ext,
        )

        if self.settings.debug_output_dir:
            await self._save_debug_copy(output_path, filename)

        logger.info(
            "render.complete",
            bytes=len(output_bytes),
        )

        return RenderResult(
            bytes=output_bytes,
            content_type=content_type,
            filename=filename,
            disposition=disposition,
        )

    async def _finalise_archive(
        self,
        layout: _ProjectLayout,
        plan: _OutputPlan,
        output: RenderOutput,
    ) -> RenderResult:
        archive_path = layout.output_path("zip")
        output_bytes, file_count = await asyncio.to_thread(
            self._create_archive,
            plan.typst_output,
            archive_path,
            plan.extension,
        )
        filename = self._sanitize_filename(output.filename or "rendered.zip", ext="zip")

        if self.settings.debug_output_dir:
            await self._save_debug_copy(archive_path, filename)

        logger.info("render.complete", bytes=len(output_bytes), files=file_count)
        return RenderResult(
            bytes=output_bytes,
            content_type=plan.content_type,
            filename=filename,
            disposition=plan.disposition,
        )

    def _create_archive(
        self,
        output_template: Path,
        archive_path: Path,
        extension: str,
    ) -> tuple[bytes, int]:
        outputs = self._collect_page_outputs(output_template)
        if not outputs:
            raise RenderError("Typst did not produce page output files")
        if len(outputs) > self.settings.max_output_files:
            raise TooManyOutputFilesError(count=len(outputs), max_allowed=self.settings.max_output_files)

        output_size = sum(size for _, _, size in outputs)
        self._check_output_size(output_size)

        padding = max(2, len(str(outputs[-1][0])))
        with zipfile.ZipFile(archive_path, mode="w") as archive:
            for page, path, _size in outputs:
                entry = zipfile.ZipInfo(f"page-{page:0{padding}d}.{extension}", date_time=_ZIP_TIMESTAMP)
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.create_system = 3
                entry.external_attr = _ZIP_FILE_MODE << 16
                archive.writestr(entry, path.read_bytes())

        archive_size = archive_path.stat().st_size
        self._check_output_size(archive_size)
        return archive_path.read_bytes(), len(outputs)

    def _check_output_size(self, size: int) -> None:
        if size > self.settings.max_output_bytes:
            raise OutputTooLargeError(size=size, max_allowed=self.settings.max_output_bytes)

    def _collect_page_outputs(self, output_template: Path) -> list[tuple[int, Path, int]]:
        prefix, suffix = output_template.name.split("{p}", maxsplit=1)
        pattern = re.compile(rf"{re.escape(prefix)}([1-9]\d*){re.escape(suffix)}")
        outputs: list[tuple[int, Path, int]] = []
        for path in output_template.parent.iterdir():
            match = pattern.fullmatch(path.name)
            if match is None:
                continue
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise RenderError("Typst produced an invalid page output")
            outputs.append((int(match.group(1)), path, metadata.st_size))
        return sorted(outputs)

    async def _run_typst(
        self,
        layout: _ProjectLayout,
        output_path: Path,
        output: RenderOutput | None = None,
        *,
        page_selection: str | None = None,
    ) -> None:
        bound_template, project_root = layout.bound_template, layout.project_root
        package_dir = layout.package_dir
        package_dir.mkdir(parents=True, exist_ok=True)
        output = output or PdfOutput()
        font_paths = [project_root]
        if self.settings.font_path is not None:
            font_paths.append(self.settings.font_path)
        package_path = self.settings.local_package_path or package_dir
        proc = await asyncio.create_subprocess_exec(
            str(self.settings.cli_path),
            "compile",
            "--root",
            str(project_root),
            "--font-path",
            os.pathsep.join(str(path) for path in font_paths),
            "--package-path",
            str(package_path),
            "--package-cache-path",
            str(package_dir),
            *self._typst_output_args(output, page_selection=page_selection),
            str(bound_template),
            str(output_path),
            cwd=project_root,
            env=self._typst_env(),
            # Typst diagnostics quote the caller's source. They must not enter logs or accumulate
            # unboundedly in memory, so discard both streams rather than piping them to communicate().
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        try:
            _ = await asyncio.wait_for(
                proc.wait(),
                timeout=self.settings.render_timeout_secs,
            )
        except TimeoutError as exc:
            proc.kill()
            _ = await proc.wait()
            raise RenderTimeoutError("Render timed out") from exc
        except asyncio.CancelledError:
            proc.kill()
            _ = await proc.wait()
            raise

        if proc.returncode != 0:
            error = InlineTemplateError(f"Template rendering failed (exit code {proc.returncode})")
            log_method = logger.error if status_is_server_fault(error.status) else logger.warning
            log_method(
                "typst.failed",
                returncode=proc.returncode,
                template=str(bound_template),
            )
            raise error

        if "{p}" in output_path.name:
            outputs = await asyncio.to_thread(self._collect_page_outputs, output_path)
            if outputs:
                return
        elif await asyncio.to_thread(output_path.exists):
            return

        if isinstance(output, (PngOutput, SvgOutput)) and (output.page is not None or output.pages is not None):
            raise InlineTemplateError("Selected image pages do not exist")
        raise RenderError("Typst did not produce output file")

    def _typst_output_args(self, output: RenderOutput, *, page_selection: str | None = None) -> tuple[str, ...]:
        if isinstance(output, PdfOutput):
            args: list[str] = []
            standards = [standard.value for standard in output.standards]
            if output.version is not None:
                standards.insert(0, output.version.value)
            if standards:
                args.extend(("--pdf-standard", ",".join(standards)))
            if output.pages is not None:
                args.extend(("--pages", output.pages))
            return tuple(args)

        if isinstance(output, PngOutput):
            args = ["--ppi", str(output.ppi)]
            selected_pages = page_selection or (str(output.page) if output.page is not None else None)
            if selected_pages is not None:
                args.extend(("--pages", selected_pages))
            return tuple(args)

        if isinstance(output, SvgOutput):
            selected_pages = page_selection or (str(output.page) if output.page is not None else None)
            if selected_pages is not None:
                return "--pages", selected_pages
        return ()

    def _typst_env(self) -> dict[str, str]:
        """
        Build the environment for the typst subprocess.

        The compiler runs caller-supplied source, so it inherits an allowlist rather than this
        process's environment. Caller sources may also contain '@preview/...' imports, which typst
        resolves by downloading code at compile time. Redirecting every proxy variable at a closed
        port denies that without a deployment-level egress rule; NO_PROXY is cleared so an operator
        value cannot exempt the package host.
        """
        env = {name: value for name, value in os.environ.items() if name in _TYPST_ENV_ALLOWLIST}
        if self.settings.allow_typst_network:
            return env

        for name in ("http_proxy", "https_proxy", "all_proxy"):
            env[name] = _BLACKHOLE_PROXY
            env[name.upper()] = _BLACKHOLE_PROXY
        env["NO_PROXY"] = ""
        env["no_proxy"] = ""
        return env

    async def _save_debug_copy(self, output_path: Path, filename: str) -> None:
        try:
            target_dir = self.settings.debug_output_dir
            if not target_dir:
                return
            target_dir.mkdir(parents=True, exist_ok=True)
            safe_filename = self._sanitize_component(Path(filename).name)[:_MAX_DEBUG_FILENAME_LENGTH]
            target = target_dir / f"{_DEBUG_PREFIX}-{uuid4().hex[:_DEBUG_UUID_HEX_LENGTH]}-{safe_filename}"
            _ = await asyncio.to_thread(shutil.copyfile, output_path, target)
            logger.info("debug.copy_saved", target=str(target))
        except Exception as exc:
            logger.warning("debug.copy_failed", error=str(exc))

    def _sanitize_filename(self, name: str, *, ext: str) -> str:
        basename = Path(name).name
        filtered = "".join(ch for ch in basename if ch in SAFE_FILENAME_CHARS).strip("._-")

        if not filtered:
            stem = "rendered"
        else:
            parts = [part for part in filtered.split(".") if part]
            stem = parts[0] if parts else "rendered"

        stem = stem[:_MAX_FILENAME_STEM_LENGTH]
        safe_ext = ext.lstrip(".").lower() or "pdf"
        return f"{stem}.{safe_ext}"

    def _sanitize_component(self, value: str) -> str:
        cleaned = "".join(char if char.isascii() and (char.isalnum() or char in "._-") else "_" for char in value)
        return cleaned.strip("_") or "unknown"

    def _json_to_typst(self, value: JSONValue) -> str:
        if value is None:
            return "none"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return json.dumps(value)
        if isinstance(value, str):
            return f'"{self._escape_string(value)}"'
        if isinstance(value, list):
            inner = ", ".join(self._json_to_typst(v) for v in value)
            return f"({inner},)" if inner else "()"
        parts: list[str] = []
        for key, val in value.items():
            parts.append(f'"{self._escape_string(str(key))}": {self._json_to_typst(val)}')
        return f"({', '.join(parts)})" if parts else "(:)"

    def _escape_string(self, input_str: str) -> str:
        """
        Escape a string for inclusion in Typst code, per the _TYPST_ESCAPES table.

        :raises StringTooLargeError: If the string exceeds max_string_bytes.
        """
        # ASCII is the common case and its length is its byte count, so the copy is skippable.
        # surrogatepass keeps the guard from raising on lone surrogates, which the table strips.
        string_bytes = len(input_str) if input_str.isascii() else len(input_str.encode("utf-8", errors="surrogatepass"))
        if string_bytes > self.settings.max_string_bytes:
            raise StringTooLargeError(
                size=string_bytes,
                max_allowed=self.settings.max_string_bytes,
            )

        return input_str.translate(_TYPST_ESCAPES)
