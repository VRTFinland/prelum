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
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.config import Settings
from app.core.constants import INLINE_TEMPLATE_FILENAME, SAFE_FILENAME_CHARS
from app.core.constraints import files_key_rules
from app.core.errors import InvalidFileDataError, InvalidTemplatePathError
from app.models import RenderFile
from app.render.renderer import TypstRenderer
from app.render.templates import (
    DEFAULT_RENDER_TEMP_ROOT,
    INLINE_FILE_KEY_PATTERN,
    MAX_KEY_LENGTH,
    MAX_KEY_SEGMENT_LENGTH,
    PROJECT_ROOT_RESERVE,
    RENDER_TEMP_PREFIX,
    SAFE_SEGMENT_CHARACTER_CLASS,
    ensure_project_root_fits,
    validate_inline_file_key,
)

# Segments chosen to hit every branch of the validator and every way a path can be hostile:
# ordinary names, the reserved entry point in three spellings, dot-only segments, empty segments,
# characters outside the safe set, a denied suffix, a name past the usual 255-byte limit, and a name
# exactly at it — four of those join into a key past MAX_KEY_LENGTH, so the whole-key bound is
# straddled by the pool rather than left to a rule nothing generates a witness for.
SEGMENTS = [
    "lib",
    "utils.typ",
    "logo.png",
    # The entry point in two spellings: rejected for the character outside SAFE_FILENAME_CHARS that
    # makes it unspellable as a key, which is the whole of its protection.
    INLINE_TEMPLATE_FILENAME,
    INLINE_TEMPLATE_FILENAME.upper(),
    # Accepted, and once were not: while the entry point was written to 'main.typ' these collided
    # with it. They stay in the pool as ordinary names that a stale rule would wrongly reject.
    "main.typ",
    "MAIN.TYP",
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
    "x" * 300,
    "a" * MAX_KEY_SEGMENT_LENGTH,
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
    - ``"~main.typ"`` is where the inline source is written last, so the entry silently loses to it
    - ``"~MAIN.TYP"`` is that same file on a case-insensitive volume, which ``resolve()`` cannot see
    - ``"~main.typ/foo.typ"`` would turn the entry point into a directory
    - ``"lib//utils.typ"`` normalises to another key, so two entries claim one path

    Nothing rejects the entry point by name: ``INLINE_TEMPLATE_FILENAME`` holds a character outside
    ``SAFE_FILENAME_CHARS``, so the character rule refuses every spelling of it. The last three
    assertions are what keep that true if the constant ever changes.
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


# The published rules, applied exactly as a caller in another language would apply them: match the
# pattern, and nothing else — if this helper needs a check the pattern does not provide, the
# publication is incomplete.
_PUBLISHED_PATTERN = re.compile(INLINE_FILE_KEY_PATTERN)


def _accepted_by_published_rules(key: str) -> bool:
    return bool(_PUBLISHED_PATTERN.match(key))


def _accepted_by_validator(key: str) -> bool:
    try:
        _ = validate_inline_file_key(key)
    except InvalidTemplatePathError:
        return False
    return True


def test_published_character_class_states_the_enforced_safe_set():
    """The class inside the pattern and the frozenset the validator checks are one statement."""
    matcher = re.compile(rf"{SAFE_SEGMENT_CHARACTER_CLASS}\z")
    described = {chr(code) for code in range(0x300) if matcher.match(chr(code))}

    assert described == set(SAFE_FILENAME_CHARS)


@settings(max_examples=2000)
@given(keys)
def test_published_rules_accept_exactly_what_the_validator_accepts(key: str):
    """The targeted generator: hostile segments someone thought to write down."""
    assert _accepted_by_published_rules(key) == _accepted_by_validator(key)


# Characters deliberately outside SAFE_FILENAME_CHARS — a backslash, a colon, a space, a tab, the
# two line terminators, a NUL, a combining mark, a direction override, non-ASCII letters — because a
# pool-only property passes vacuously against a future rule about a character the pool never
# generates. AGENTS.md records three defects here that were each a missing segment in that pool, and
# the newline found a fourth: '$' matches before a trailing one, so the published pattern accepted
# keys the validator rejects until it was anchored with '\z'.
_WIDE_ALPHABET = "aZ09._-/" + "\\: \t\n\r\x00\u0301\u202e\u00e4\u00e9"


@settings(max_examples=5000)
@given(st.text(alphabet=_WIDE_ALPHABET, min_size=0, max_size=12))
def test_published_rules_match_the_validator_outside_the_segment_pool(key: str):
    """The untargeted generator: characters nobody thought to write down."""
    assert _accepted_by_published_rules(key) == _accepted_by_validator(key)


# The translation docs/api/files-key-rules.md gives for Python 3.13 and earlier, where the re module
# does not yet know '\z' and raises on it. Python's '\Z' already means the absolute end of the
# string — unlike PCRE's and Java's, which is why the published expression cannot simply use it —
# so the substitution is exact rather than approximate. It also models JavaScript's '$' applied
# without the m flag, that engine's documented translation, which Python cannot express directly
# because its own '$' is the lenient one.
_TRANSLATED_PATTERN = re.compile(INLINE_FILE_KEY_PATTERN.replace(r"\z", r"\Z"))


# Ruby's '^' and '$' are line anchors with no flag to turn that off, so Ruby reads the published
# pattern the way Python does with MULTILINE, and its match? scans rather than anchoring at zero the
# way Python's match() does. files-key-rules.md promises Ruby takes the expression as written, so
# that promise is only kept while the expression carries absolute anchors at both ends.
_RUBY_READING = re.compile(INLINE_FILE_KEY_PATTERN, re.MULTILINE)


def _accepted_where_anchors_are_line_anchors(key: str) -> bool:
    return bool(_RUBY_READING.search(key))


@settings(max_examples=5000)
@given(st.text(alphabet=_WIDE_ALPHABET, min_size=0, max_size=12))
def test_the_pattern_reads_the_same_where_anchors_are_always_line_anchors(key: str):
    """The runtimes promised the expression as written include one whose anchors are never absolute."""
    assert _accepted_where_anchors_are_line_anchors(key) == _accepted_by_validator(key)


def test_the_published_pattern_carries_no_line_anchor():
    """
    '^' and '$' mean start- and end-of-line in Ruby, and start- and end-of-string almost everywhere
    else. An expression published for five named runtimes cannot afford an anchor whose meaning is
    one of those two depending on the reader.
    """
    assert INLINE_FILE_KEY_PATTERN.startswith("\\A")
    assert "^" not in INLINE_FILE_KEY_PATTERN, "a negated character class needs its own decision here"
    assert "$" not in INLINE_FILE_KEY_PATTERN


@settings(max_examples=5000)
@given(st.text(alphabet=_WIDE_ALPHABET, min_size=0, max_size=12))
def test_the_documented_anchor_translation_stays_equivalent(key: str):
    """
    A published translation that is not equivalent is worse than none.

    The caller applies it believing the documentation, so a divergence ships as a validator that
    disagrees with the service while wearing its authority — the same failure as a wrong vector.
    """
    assert bool(_TRANSLATED_PATTERN.match(key)) == bool(_PUBLISHED_PATTERN.match(key))


def _longest_accepted_key() -> str:
    """A normalised key of exactly MAX_KEY_LENGTH, every segment inside MAX_KEY_SEGMENT_LENGTH."""
    segments = ["a" * 100] * (MAX_KEY_LENGTH // 101)
    key = "/".join(segments)
    return key + "/" + "a" * (MAX_KEY_LENGTH - len(key) - 1)


def test_the_controlled_project_root_fits_inside_its_reserve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """An operator's TMPDIR cannot change the root behind the published key-length contract."""
    long_tmpdir = tmp_path / ("operator-controlled-" * 12)
    long_tmpdir.mkdir()
    monkeypatch.setenv("TMPDIR", str(long_tmpdir))

    with tempfile.TemporaryDirectory(prefix=RENDER_TEMP_PREFIX, dir=DEFAULT_RENDER_TEMP_ROOT) as temp_dir:
        project_root = Path(temp_dir) / "project"
        ensure_project_root_fits(project_root)

    assert not project_root.is_relative_to(long_tmpdir)


def test_the_reserve_is_measured_against_the_resolved_path(tmp_path: Path):
    """
    PATH_MAX applies to the path the kernel resolves, not the one the process spells.

    A symlinked temp root — which is what /tmp is on macOS — makes the real path longer than the
    spelling, so measuring the spelling proves less than the guard's docstring claims.
    """
    real_root = tmp_path / ("d" * (PROJECT_ROOT_RESERVE - 4))
    real_root.mkdir()
    link = tmp_path / "s"
    link.symlink_to(real_root)

    with pytest.raises(RuntimeError, match="render project root needs"):
        ensure_project_root_fits(link)


def test_a_project_root_past_its_reserve_fails_before_writing_caller_files(tmp_path: Path):
    project_root = tmp_path / ("x" * PROJECT_ROOT_RESERVE)

    with pytest.raises(RuntimeError, match="render project root needs"):
        ensure_project_root_fits(project_root)


def test_a_key_at_the_length_limit_is_actually_written():
    """
    Accepting a key the filesystem cannot hold is a bug the client error hides.

    ``MAX_KEY_LENGTH`` reserves room for the absolute project root, so a key at the limit must
    reach disk rather than come back as 'cannot be written' after the entry was already decoded.
    """
    key = _longest_accepted_key()
    assert _validate(key) == key

    with tempfile.TemporaryDirectory(prefix=RENDER_TEMP_PREFIX, dir=DEFAULT_RENDER_TEMP_ROOT) as temp_dir:
        project_root = Path(temp_dir) / "project"
        project_root.mkdir()

        _RENDERER._write_inline_files(project_root, {key: RenderFile(encoding="text", content="content")})

        assert (project_root / key).read_bytes() == b"content"


# Both derived from the published document once at import rather than per example. The document is
# immutable, so rebuilding it for each of the 7,000 cases these two properties run bought nothing —
# it is cheap enough not to show in the runtime, which Hypothesis itself dominates, so this is for
# clarity rather than speed. The mirror below still reads nothing but published data fields.
_PUBLISHED_RULES: dict[str, Any] = files_key_rules().published()
_PUBLISHED_SEGMENT_CHARACTER = re.compile(_PUBLISHED_RULES["segment_character_class"])


# A consumer that cannot compile key_pattern at all — Go's regexp and Rust's regex are RE2 and have
# no lookahead — has only the published data. This is that consumer, written from the data fields
# alone: if a rule the validator enforces is missing here, the document does not contain it either.
def _accepted_by_published_data(key: str) -> bool:
    rules = _PUBLISHED_RULES
    if not (rules["min_key_length"] <= len(key) <= rules["max_key_length"]):
        return False
    for segment in key.split("/"):
        if not (rules["min_segment_length"] <= len(segment) <= rules["max_segment_length"]):
            return False
        if not all(_PUBLISHED_SEGMENT_CHARACTER.match(character) for character in segment):
            return False
        if rules["segments_may_not_be_only_dots"] and segment and all(c == "." for c in segment):
            return False
    return True


@settings(max_examples=2000)
@given(keys)
def test_the_published_data_alone_accepts_exactly_what_the_validator_accepts(key: str):
    """The expression is a fast path, so the data has to carry every rule on its own."""
    assert _accepted_by_published_data(key) == _accepted_by_validator(key)


@settings(max_examples=5000)
@given(st.text(alphabet=_WIDE_ALPHABET, min_size=0, max_size=12))
def test_the_published_data_alone_matches_the_validator_outside_the_segment_pool(key: str):
    assert _accepted_by_published_data(key) == _accepted_by_validator(key)


def test_the_reserve_is_measured_against_the_longer_of_the_two_spellings(tmp_path: Path):
    """
    Resolving alone traded one under-measurement for the opposite one.

    The kernel applies its length limit to the resolved path, but ENAMETOOLONG is raised against the
    pathname the syscall actually carries — which is the spelling. A long root that resolves short
    would pass the guard while every render still spells the long one, and a key at the published
    MAX_KEY_LENGTH would then break the contract that /v1/constraints promises.
    """
    target = tmp_path / "t"
    target.mkdir()
    spelled_long = tmp_path / ("d" * 120) / ("d" * 120)
    spelled_long.parent.mkdir(parents=True)
    spelled_long.symlink_to(target)

    assert len(os.fsencode(spelled_long)) > PROJECT_ROOT_RESERVE
    with pytest.raises(RuntimeError, match="render project root needs"):
        ensure_project_root_fits(spelled_long)
