from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from app.core.constants import SAFE_FILENAME_CHARS
from app.core.errors import InvalidFileDataError, InvalidTemplatePathError, for_message

# POSIX NAME_MAX. Declared here rather than discovered from the filesystem, so a key that no
# filesystem would accept is rejected with the same message everywhere.
MAX_KEY_SEGMENT_LENGTH = 255

# POSIX PATH_MAX, at the smallest value in use (macOS and the BSDs; Linux allows 4096). Declared
# statically for the same reason as the segment bound, so the verdict does not vary by host.
MAX_PATH_LENGTH = 1024

# Render projects live below a short, ASCII-only root rather than an operator-controlled TMPDIR:
# the published key-length bound is derived from how much of the path limit the root leaves. The
# default suits a container; PRELUM_TEMP_ROOT redirects it for a deployment whose /tmp is read-only
# or memory-backed, and Settings rejects a root that would not leave the reserve intact.
DEFAULT_RENDER_TEMP_ROOT = Path("/tmp")  # noqa: S108 - TemporaryDirectory creates the owned child securely
RENDER_TEMP_PREFIX = "prelum-"

# What TemporaryDirectory adds below the root before caller files are written: the prefix, the eight
# random characters it appends, and the project directory the renderer creates inside it.
_RENDER_ROOT_SUFFIX = f"{RENDER_TEMP_PREFIX}{'X' * 8}/project"

# A key is relative, but what reaches the filesystem is the absolute project root plus a separator,
# the key and a terminating NUL. The reserve leaves room for all three; ensure_project_root_fits
# asserts the actual render root before caller files are written.
PROJECT_ROOT_RESERVE = 256

# Without a whole-key bound a key is limited only per segment: 'a/a/a/…' passes every other rule at
# any length, is written to a real filesystem only to fail as ENAMETOOLONG once the entry has
# already been decoded, and reaches error responses and log lines whole.
MAX_KEY_LENGTH = MAX_PATH_LENGTH - PROJECT_ROOT_RESERVE


def ensure_temp_root_fits(temp_root: Path) -> None:
    """
    Fail if renders below this root could not honour the published key-length bound.

    Applied to the configured root at startup rather than to the project root per render, so an
    operator who redirects the scratch space learns of it before the first request rather than as
    a 503 after it.

    :raises RuntimeError: If the deepest path a render builds would not fit the reserve.
    """
    ensure_project_root_fits(temp_root / _RENDER_ROOT_SUFFIX)


def ensure_project_root_fits(project_root: Path) -> None:
    """Fail as an infrastructure error if the controlled render root voids the key-length contract."""
    # Both spellings have to fit. The kernel resolves symlinks before applying its own path limit,
    # so a short spelling of a long target understates the real length — /tmp resolves to
    # /private/tmp on macOS. But ENAMETOOLONG is raised against the pathname the syscall carries,
    # so a long spelling of a short target understates it in the other direction. Measure the
    # longer. resolve() is non-strict, because the root is measured before it is created.
    spellings = (os.fsencode(project_root), os.fsencode(project_root.resolve()))
    required = max(len(spelling) for spelling in spellings) + 2  # separator plus terminating NUL
    if required > PROJECT_ROOT_RESERVE:
        raise RuntimeError(f"render project root needs {required} bytes; reserved maximum is {PROJECT_ROOT_RESERVE}")


# Validation is O(keys x depth) and a 20 MB body holds hundreds of thousands of short keys, so the
# count is bounded before that work starts. PRELUM_MAX_INLINE_FILES is the configurable limit.
MAX_INLINE_FILE_KEYS = 1024

# The published form of the per-key rules, for callers that validate before dispatch. Every check in
# validate_inline_file_key except the denied suffix collapses into this one expression: restricting
# the character set makes the absolute-path and normalised-form checks redundant, because a leading
# '/', '//', './', a trailing '/' and '..' all fail it. tests/test_inline_file_key_properties.py pins
# the two together over a targeted and an untargeted generator, so a new rule changes both or
# neither. Built from the constants above rather than typed out, so a limit cannot drift numerically.
SAFE_SEGMENT_CHARACTER_CLASS = "[A-Za-z0-9._-]"

# Both ends are anchored absolutely rather than with ^ and $, which are line anchors: $ matches
# before a trailing newline in Python, PCRE and Java, and Ruby reads ^ and $ as line anchors always,
# with no flag to turn that off. Either one accepts a key carrying a newline — 'lib/label.typ\n' at
# the end, 'bad\nlib/label.typ' at the start — that the validator rejects, because a newline is
# outside SAFE_FILENAME_CHARS. That is the drift this publication exists to prevent, so the anchors
# are the strict ones everywhere they appear.
_START = r"\A"
_END = r"\z"

_SEGMENT = rf"(?!\.+(?:/|{_END})){SAFE_SEGMENT_CHARACTER_CLASS}{{1,{MAX_KEY_SEGMENT_LENGTH}}}"

# The whole-key bound has to be a lookahead: it constrains a total length no per-segment expression
# can see, and without it the published pattern accepts keys the validator rejects — a caller
# mirroring it would ship a key and receive an opaque 400. '.' never matches a newline, and a key
# holding one already fails the character class, so the two still agree on those.
_WITHIN_KEY_LENGTH = rf"(?=.{{1,{MAX_KEY_LENGTH}}}{_END})"

INLINE_FILE_KEY_PATTERN = rf"{_START}{_WITHIN_KEY_LENGTH}{_SEGMENT}(?:/{_SEGMENT})*{_END}"


def validate_inline_file_key(key: str) -> str:
    """
    Validate one caller-supplied files key, which the renderer turns into a file path.

    An accepted key comes back unchanged and is safe to append to the render project root; each
    check below says what it rules out. POSIX semantics are applied on every platform, so the
    guarantee is a property of this function rather than of the deployment target.

    There is no rule protecting the inline entry point: INLINE_TEMPLATE_FILENAME holds a character
    outside SAFE_FILENAME_CHARS, so the character check below already makes every spelling of it
    unrepresentable as a key. A rule here would be a second statement of the same thing.

    See ``validate_inline_file_keys`` for the rules that need the whole set of keys.

    :param key: A relative path such as 'lib/utils.typ'.
    :raises InvalidTemplatePathError: If the key is invalid or unsafe.
    """
    # Both guards are redundant against the checks further down; they exist for a better message.
    if not key:
        raise InvalidTemplatePathError("files key cannot be empty")

    # Bounded before the path is parsed: every check below is O(length), and this is the one rule
    # whose whole point is that the key may be arbitrarily long.
    if len(key) > MAX_KEY_LENGTH:
        raise InvalidTemplatePathError(f"files key '{for_message(key)}' is longer than {MAX_KEY_LENGTH} characters")

    path = PurePosixPath(key)

    if path.is_absolute():
        raise InvalidTemplatePathError(f"files key '{for_message(key)}' must be a relative path")

    # Path('.') and Path('./') normalise to no parts at all, which would make the segment
    # loop below vacuous and resolve to the project root itself at write time.
    if not path.parts:
        raise InvalidTemplatePathError(f"files key '{for_message(key)}' does not name a file")

    # Keys differing only in separators or dot segments resolve to one destination, where the later
    # entry would silently overwrite the earlier. This does not subsume the checks around it: '.'
    # and '..' are already their own normalised form.
    if PurePosixPath(*path.parts).as_posix() != key:
        raise InvalidTemplatePathError(f"files key '{for_message(key)}' is not in normalised form")

    for part in path.parts:
        if all(c == "." for c in part):
            raise InvalidTemplatePathError(f"files key '{for_message(key)}' contains invalid segment '{part}'")
        if not all(c in SAFE_FILENAME_CHARS for c in part):
            raise InvalidTemplatePathError(
                f"files key '{for_message(key)}' contains invalid characters in segment '{part}'"
            )
        if len(part) > MAX_KEY_SEGMENT_LENGTH:
            raise InvalidTemplatePathError(
                f"files key '{for_message(key)}' has a segment longer than {MAX_KEY_SEGMENT_LENGTH}"
            )

    return key


def ensure_key_count_fits(count: int) -> None:
    """
    Refuse a files mapping with more keys than the structural cap allows.

    Split out of the whole-mapping validation so the model can apply it to the raw request, before
    Pydantic has validated a single entry: the cap is the one rule that needs nothing but the
    mapping's size, and applying it late is what let an oversized mapping be fully validated first.

    :raises InvalidFileDataError: If the count exceeds MAX_INLINE_FILE_KEYS.
    """
    if count > MAX_INLINE_FILE_KEYS:
        raise InvalidFileDataError(
            f"Too many files keys: {count} exceeds the limit of {MAX_INLINE_FILE_KEYS}",
            context={"count": count, "limit": MAX_INLINE_FILE_KEYS, "rule": "too_many_keys"},
        )


def validate_inline_file_keys(keys: Iterable[str]) -> list[str]:
    """
    Validate the whole files mapping, called by the model once per request.

    The rules here are the ones a single key cannot answer: two keys can each be valid alone and
    still describe something no filesystem can hold — one used as a file and as a directory — or
    something that depends on the host, when they differ only in case. Both would otherwise
    surface as a failed write rather than a rejected request.

    :param keys: In request order, which the returned list preserves.
    :raises InvalidTemplatePathError: If an individual key is invalid.
    :raises InvalidFileDataError: If there are more than MAX_INLINE_FILE_KEYS keys or two keys conflict.
    """
    keys = list(keys)
    ensure_key_count_fits(len(keys))

    validated = [validate_inline_file_key(key) for key in keys]

    claimed: dict[str, str] = {}
    for key in validated:
        folded = key.casefold()
        previous = claimed.setdefault(folded, key)
        if previous != key:
            raise InvalidFileDataError(
                f"files keys '{for_message(previous)}' and '{for_message(key)}' differ only in case",
                context={"key": key, "rule": "case_collision"},
            )

    for key in validated:
        for ancestor in PurePosixPath(key).parents:
            conflicting = claimed.get(ancestor.as_posix().casefold())
            if conflicting is not None:
                message = f"files key '{for_message(conflicting)}' is also used as a directory by '{for_message(key)}'"
                raise InvalidFileDataError(message, context={"key": key, "rule": "ancestor_collision"})

    return validated
