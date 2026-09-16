"""Shared fixtures for the prelum test suite."""

import os

# Settings is fail-closed: without an explicit dev environment it refuses to construct. Declaring
# it here rather than per-test keeps the production rule intact for the tests that assert on it,
# which clear the variable through monkeypatch.
os.environ.setdefault("PRELUM_ENVIRONMENT", "test")

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.constants import INLINE_TEMPLATE_FILENAME
from app.render.renderer import TypstRenderer

# Template and output are addressed from the end of typst's argv, so a wrapper prefix cannot move
# them. Kept here rather than in each test so the convention is stated once.
_ARGV_TEMPLATE = -2
_ARGV_OUTPUT = -1


@pytest.fixture
def make_renderer() -> Callable[..., TypstRenderer]:
    """
    Build a renderer with per-test settings overrides.

    Keeps the collaborator wiring in one place: every test that needs a renderer otherwise repeats
    the same Settings/TypstRenderer pair.
    """

    def factory(**settings_overrides: object) -> TypstRenderer:
        settings = Settings(**settings_overrides)
        return TypstRenderer(settings)

    return factory


class FakeProc:
    """
    Stands in for asyncio.subprocess.Process, settling immediately unless asked to block.

    :param blocks_for: Seconds to hang in wait() until killed, for the cancel and timeout paths.
    """

    def __init__(self, returncode: int = 0, *, blocks_for: float | None = None) -> None:
        self.returncode: int = returncode
        self.blocks_for: float | None = blocks_for
        self.killed: bool = False

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        # asyncio.Process.wait() returns the settled exit code and returncode then reports the same
        # value, so both read the one attribute: a fake must not be able to make them disagree.
        if self.blocks_for is not None and not self.killed:
            await asyncio.sleep(self.blocks_for)
        return self.returncode


@dataclass(frozen=True, slots=True)
class TypstCall:
    """
    One recorded typst invocation: its argv, its process options, and the project it was handed.

    `files` is snapshotted while the call is in flight because the renderer removes the temporary
    project before the test regains control, so this is the only point the contents can be read.
    """

    argv: tuple[str, ...]
    kwargs: Mapping[str, object]
    files: Mapping[str, bytes]

    @property
    def template(self) -> Path:
        """The bound template typst was told to compile."""
        return Path(self.argv[_ARGV_TEMPLATE])

    @property
    def output(self) -> Path:
        """The output path, still carrying the '{p}' placeholder for a multi-page run."""
        return Path(self.argv[_ARGV_OUTPUT])

    @property
    def project_root(self) -> Path:
        """The temporary project, which only exists while the call is in flight."""
        return self.template.parent

    @property
    def source(self) -> str:
        """The bound template's text: the prelude the renderer generated, then the caller's source."""
        return self.files[INLINE_TEMPLATE_FILENAME].decode("utf-8")

    def page(self, number: int) -> Path:
        return Path(str(self.output).replace("{p}", str(number)))


def _snapshot_project(project_root: Path) -> dict[str, bytes]:
    """
    Every file under the temporary project, keyed by its project-relative posix path.

    Tolerates a root that does not exist: the tests that drive _run_typst directly build a layout
    by hand and never write a project.

    Reads through open() rather than Path.read_bytes because this is the fake's own bookkeeping. A
    test that forbids the *renderer* from reading a file does so by patching Path.read_bytes, and
    must not be tripped by the snapshot taken on its behalf.
    """
    if not project_root.is_dir():
        return {}
    snapshot: dict[str, bytes] = {}
    for path in sorted(project_root.rglob("*")):
        if path.is_file():
            with path.open("rb") as handle:
                snapshot[path.relative_to(project_root).as_posix()] = handle.read()
    return snapshot


class FakeTypst:
    """The invocations a patched typst compiler received."""

    def __init__(self) -> None:
        self.calls: list[TypstCall] = []

    @property
    def call(self) -> TypstCall:
        """The single invocation, for the tests that assert exactly one compile ran."""
        assert len(self.calls) == 1, f"expected exactly one typst invocation, got {len(self.calls)}"
        return self.calls[0]


@pytest.fixture
def fake_typst(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeTypst]:
    """
    Patch the typst subprocess, recording each invocation and writing the output asked of it.

    No renderer test runs the real compiler, so the only things that vary between them are what the
    compile writes, how the process settles, and what the test needs to see. Everything else — the
    argv indices, creating the output, returning a process — was hand-rolled per test before this.

    :param content: Bytes for a single-file output; None produces no output at all.
    :param pages: Page number to bytes for a multi-page ('{p}') output, instead of `content`.
    :param proc: The process to return, for a non-zero exit, a signal, or a blocking wait.
    """

    def install(
        *,
        content: bytes | None = b"%PDF-fake",
        pages: Mapping[int, bytes] | None = None,
        proc: FakeProc | None = None,
    ) -> FakeTypst:
        recorded = FakeTypst()

        async def fake_run(*argv: object, **kwargs: object) -> FakeProc:
            argv_strings = tuple(str(part) for part in argv)
            call = TypstCall(
                argv=argv_strings,
                kwargs=kwargs,
                files=_snapshot_project(Path(argv_strings[_ARGV_TEMPLATE]).parent),
            )
            recorded.calls.append(call)
            if pages is not None:
                for number, page_content in pages.items():
                    _ = call.page(number).write_bytes(page_content)
            elif content is not None:
                _ = call.output.write_bytes(content)
            return proc if proc is not None else FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_run)
        return recorded

    return install
