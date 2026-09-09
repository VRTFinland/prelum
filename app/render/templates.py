from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath

from app.core.constants import INLINE_TEMPLATE_FILENAME, SAFE_FILENAME_CHARS
from app.core.errors import InvalidFileDataError, InvalidTemplatePathError, for_message

# Typst's plugin() instantiates such a file as code. It finds plugins by magic bytes rather than
# by name, so rejecting the suffix only stops a file actually named .wasm: a speed bump, not a wall.
DENIED_INLINE_SUFFIXES = (".wasm",)

# POSIX NAME_MAX. Declared here rather than discovered from the filesystem, so a key that no
# filesystem would accept is rejected with the same message everywhere.
MAX_KEY_SEGMENT_LENGTH = 255

# Validation is O(keys x depth) and a 20 MB body holds hundreds of thousands of short keys, so the
# count is bounded before that work starts. PRELUM_MAX_INLINE_FILES is the configurable limit.
MAX_INLINE_FILE_KEYS = 1024


def validate_inline_file_key(key: str) -> str:
    """
    Validate one caller-supplied files key, which the renderer turns into a file path.

    An accepted key comes back unchanged and is safe to append to the render project root; each
    check below says what it rules out. POSIX semantics are applied on every platform, so the
    guarantee is a property of this function rather than of the deployment target.

    See ``validate_inline_file_keys`` for the rules that need the whole set of keys.

    :param key: The relative file path to validate (e.g. 'lib/utils.typ').
    :return: The validated key unchanged.
    :raises InvalidTemplatePathError: If the key is invalid or unsafe.
    """
    # Both guards are redundant against the checks further down; they exist for a better message.
    if not key:
        raise InvalidTemplatePathError("files key cannot be empty")

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

    # As the whole key, the inline source written last would overwrite the entry; as a leading
    # directory, the renderer would need a directory where that source goes. Case-folded, because a
    # case-insensitive volume treats 'Main.typ' as the same file. Harmless deeper ('lib/main.typ').
    if path.parts[0].casefold() == INLINE_TEMPLATE_FILENAME.casefold():
        raise InvalidTemplatePathError(f"files key '{for_message(key)}' is reserved for the render source")

    # Compared against the name rather than the suffix, which is empty for '.wasm' and 'lib/.wasm'.
    if path.parts[-1].lower().endswith(DENIED_INLINE_SUFFIXES):
        raise InvalidTemplatePathError(f"files key '{for_message(key)}' has a disallowed file type")

    return key


def validate_inline_file_keys(keys: Iterable[str]) -> list[str]:
    """
    Validate the whole files mapping, called by the model once per request.

    The rules here are the ones a single key cannot answer: two keys can each be valid alone and
    still describe something no filesystem can hold — one used as a file and as a directory — or
    something that depends on the host, when they differ only in case. Both would otherwise
    surface as a failed write rather than a rejected request.

    :param keys: The files keys, in request order.
    :return: The validated keys as a list, unchanged and in order.
    :raises InvalidTemplatePathError: If an individual key is invalid.
    :raises InvalidFileDataError: If there are more than MAX_INLINE_FILE_KEYS keys or two keys conflict.
    """
    keys = list(keys)
    if len(keys) > MAX_INLINE_FILE_KEYS:
        raise InvalidFileDataError(f"Too many files keys: {len(keys)} exceeds the limit of {MAX_INLINE_FILE_KEYS}")

    validated = [validate_inline_file_key(key) for key in keys]

    claimed: dict[str, str] = {}
    for key in validated:
        folded = key.casefold()
        previous = claimed.setdefault(folded, key)
        if previous != key:
            raise InvalidFileDataError(
                f"files keys '{for_message(previous)}' and '{for_message(key)}' differ only in case"
            )

    for key in validated:
        for ancestor in PurePosixPath(key).parents:
            conflicting = claimed.get(ancestor.as_posix().casefold())
            if conflicting is not None:
                message = f"files key '{for_message(conflicting)}' is also used as a directory by '{for_message(key)}'"
                raise InvalidFileDataError(message)

    return validated
