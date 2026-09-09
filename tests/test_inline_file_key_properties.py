"""
Property-based tests for inline files keys, built from deliberately hostile path segments.

Example-based tests cover the keys someone thought to write down; every bug this validator has had
came from one nobody did. There is a property per layer, because the layers promise different
things: the validator decides which keys are accepted, and the renderer must not turn an accepted
key into a server error — the filesystem can still refuse a well-shaped key, and that has to
surface as a client error.

Each property is an implication over the whole generated space rather than ``assume(accepted)``, so
no input is filtered away and both branches stay under test.
"""

import contextlib
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.config import Settings
from app.core.constants import INLINE_TEMPLATE_FILENAME
from app.core.errors import InvalidFileDataError, InvalidTemplatePathError
from app.models import RenderFile
from app.render.renderer import TypstRenderer
from app.render.templates import validate_inline_file_key

# Segments chosen to hit every branch of the validator and every way a path can be hostile:
# ordinary names, the reserved entry point in three spellings, dot-only segments, empty segments,
# characters outside the safe set, a denied suffix, and a name past the usual 255-byte limit.
SEGMENTS = [
    "lib",
    "utils.typ",
    "logo.png",
    INLINE_TEMPLATE_FILENAME,
    INLINE_TEMPLATE_FILENAME.upper(),
    "main",
    ".",
    "..",
    "...",
    "",
    " ",
    "a b",
    "ä",
    "back\\slash",
    "null\0byte",
    "plugin.wasm",
    ".wasm",
    "PLUGIN.WASM",
    "x" * 300,
]

# Joined with separators that produce non-normalised keys as well as plain ones, so the
# normalisation rule is exercised rather than assumed.
SEPARATORS = ["/", "//", "/./"]


@st.composite
def _keys(draw: st.DrawFn) -> str:
    segments = draw(st.lists(st.sampled_from(SEGMENTS), min_size=1, max_size=4))
    separator = draw(st.sampled_from(SEPARATORS))
    return separator.join(segments)


keys = _keys()

# The shape properties only resolve paths and never write, so one notional root serves every
# example — it is deliberately never created. The writability property does write, and keeps its
# own directory per example.
_SHAPE_ROOT = Path(tempfile.gettempdir()) / "prelum-key-shape-check" / "project"

# _write_inline_files takes its project root per call, so one renderer can serve every example.
_RENDERER = TypstRenderer(Settings())


def _validate(key: str) -> str | None:
    """Return the validated key, or None when the validator rejected it."""
    try:
        return validate_inline_file_key(key)
    except InvalidTemplatePathError:
        return None


@given(keys)
@settings(max_examples=500)
def test_accepted_key_names_exactly_the_file_the_renderer_writes(key: str):
    """
    A template writes ``#import "lib/utils.typ"``, so the key it was sent under has to be the
    path the bytes actually land on. Any key that is normalised, redirected or overwritten on the
    way to disk makes that import fail or read another entry's bytes. The assertions below rule
    each of those out:

    - ``"."``, ``"./"`` resolve to the project root, so the write hit a directory (a 503)
    - ``"../x.typ"``, ``"/etc/passwd"`` land outside the project root
    - ``"main.typ"``, ``"./main.typ"`` are where the inline source is written last, so the
      entry silently loses to it
    - ``"MAIN.TYP"`` is that same file on a case-insensitive volume, which ``resolve()`` cannot see
    - ``"main.typ/foo.typ"`` would turn the entry point into a directory
    - ``"lib//utils.typ"`` normalises to another key, so two entries claim one path
    """
    validated = _validate(key)
    if validated is None:
        return

    assert validated == key, "an accepted key must not be silently normalised"
    assert Path(*Path(key).parts).as_posix() == key, "an accepted key must be its own normalised form"

    root = _SHAPE_ROOT.resolve()
    entry_point = (_SHAPE_ROOT / INLINE_TEMPLATE_FILENAME).resolve()
    resolved = (_SHAPE_ROOT / key).resolve()

    assert resolved.is_relative_to(root), "escapes the project root"
    assert resolved != root, "resolves to the project root itself"
    assert resolved != entry_point, "would be overwritten by the inline source"
    assert entry_point not in resolved.parents, "would turn the entry point into a directory"
    # resolve() preserves case, so the assertions above cannot see a clash that only a
    # case-insensitive volume would produce.
    assert Path(key).parts[0].casefold() != INLINE_TEMPLATE_FILENAME.casefold(), (
        "would clash with the entry point on a case-insensitive volume"
    )


@given(keys)
@settings(max_examples=500)
def test_absolute_and_traversing_keys_are_always_rejected(key: str):
    if key.startswith("/") or ".." in Path(key).parts:
        assert _validate(key) is None


@given(st.text(max_size=40))
@settings(max_examples=500)
def test_validator_raises_nothing_but_invalid_template_path(key: str):
    """Any other exception type escaping the validator would surface as a 5xx, not a 400."""
    _ = _validate(key)


@given(st.lists(keys, max_size=6))
@settings(max_examples=200)
def test_writing_accepted_keys_never_raises_a_server_error(key_list: list[str]):
    """
    Writing any set of accepted keys, in any order, either succeeds or fails as a client error.

    This is the guarantee the renderer owes the route: an unhandled OSError here becomes a 503
    with a Sentry event for what is only a malformed request. The project directory has to be
    per-example — one example's file or directory would otherwise decide the next one's outcome.
    """
    accepted = [key for key in key_list if _validate(key) is not None]

    with tempfile.TemporaryDirectory() as temp_dir:
        project_root = Path(temp_dir) / "project"
        project_root.mkdir()
        files = {key: RenderFile(encoding="text", content="content") for key in accepted}

        with contextlib.suppress(InvalidFileDataError):
            _RENDERER._write_inline_files(project_root, files)


def test_segments_cover_both_outcomes():
    """Guard against the pool drifting into all-accepted or all-rejected, which would void the properties."""
    outcomes = {_validate(segment) is not None for segment in SEGMENTS}

    assert outcomes == {True, False}, "SEGMENTS must contain both accepted and rejected names"


@pytest.mark.parametrize("key", ["x" * 300, "lib/" + "x" * 300])
def test_key_longer_than_the_declared_bound_is_rejected_by_the_validator(key: str):
    """The bound is declared (MAX_KEY_SEGMENT_LENGTH), so enforcing it is the validator's job."""
    assert _validate(key) is None
