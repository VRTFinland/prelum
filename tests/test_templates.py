import pytest

from app.core.constants import INLINE_TEMPLATE_FILENAME
from app.core.errors import InvalidFileDataError, InvalidTemplatePathError
from app.render.templates import (
    MAX_INLINE_FILE_KEYS,
    MAX_KEY_LENGTH,
    validate_inline_file_key,
    validate_inline_file_keys,
)


def test_validate_inline_file_key_accepts_simple_path():
    from app.render.templates import validate_inline_file_key

    assert validate_inline_file_key("lib/utils.typ") == "lib/utils.typ"


def test_validate_inline_file_key_accepts_nested_path():
    from app.render.templates import validate_inline_file_key

    assert validate_inline_file_key("assets/images/logo.png") == "assets/images/logo.png"


def test_validate_inline_file_key_accepts_root_file():
    from app.render.templates import validate_inline_file_key

    assert validate_inline_file_key("footer.typ") == "footer.typ"


def test_validate_inline_file_key_rejects_empty():
    from app.core.errors import InvalidTemplatePathError
    from app.render.templates import validate_inline_file_key

    with pytest.raises(InvalidTemplatePathError):
        validate_inline_file_key("")


def test_validate_inline_file_key_rejects_absolute():
    from app.core.errors import InvalidTemplatePathError
    from app.render.templates import validate_inline_file_key

    with pytest.raises(InvalidTemplatePathError):
        validate_inline_file_key("/etc/passwd")


def test_validate_inline_file_key_rejects_parent_traversal():
    from app.core.errors import InvalidTemplatePathError
    from app.render.templates import validate_inline_file_key

    with pytest.raises(InvalidTemplatePathError):
        validate_inline_file_key("../secret.typ")


def test_validate_inline_file_key_rejects_traversal_in_middle():
    from app.core.errors import InvalidTemplatePathError
    from app.render.templates import validate_inline_file_key

    with pytest.raises(InvalidTemplatePathError):
        validate_inline_file_key("lib/../../../etc/shadow")


def test_validate_inline_file_key_rejects_invalid_chars():
    from app.core.errors import InvalidTemplatePathError
    from app.render.templates import validate_inline_file_key

    with pytest.raises(InvalidTemplatePathError):
        validate_inline_file_key("lib/bad file!.typ")


def test_validate_inline_file_key_rejects_multi_dot_segment():
    from app.core.errors import InvalidTemplatePathError
    from app.render.templates import validate_inline_file_key

    with pytest.raises(InvalidTemplatePathError):
        validate_inline_file_key("lib/.../etc")


def test_validate_inline_file_key_rejects_dot_only():
    with pytest.raises(InvalidTemplatePathError):
        _ = validate_inline_file_key(".")


def test_validate_inline_file_key_rejects_current_directory():
    with pytest.raises(InvalidTemplatePathError):
        _ = validate_inline_file_key("./")


def test_validate_inline_file_key_accepts_main_typ():
    """No name is reserved: the entry point is written to a name no key can spell."""
    assert validate_inline_file_key("main.typ") == "main.typ"


def test_validate_inline_file_key_rejects_main_typ_with_dot_prefix():
    """Rejected for not being in normalised form, not for the name."""
    with pytest.raises(InvalidTemplatePathError):
        _ = validate_inline_file_key("./main.typ")


def test_validate_inline_file_key_accepts_main_typ_in_subdirectory():
    assert validate_inline_file_key("lib/main.typ") == "lib/main.typ"


def test_no_files_key_can_name_the_inline_entry_point():
    """
    The invariant that replaced the reserved-name rule.

    The renderer writes the inline source to INLINE_TEMPLATE_FILENAME after the files entries, so a
    key able to spell that name would lose its bytes silently. Nothing checks for it at runtime:
    the name holds a character outside SAFE_FILENAME_CHARS, so the character rule already refuses
    every spelling. This test is what keeps that true if the constant is ever changed.
    """
    spellings = [
        INLINE_TEMPLATE_FILENAME,
        INLINE_TEMPLATE_FILENAME.upper(),
        INLINE_TEMPLATE_FILENAME.casefold(),
        f"{INLINE_TEMPLATE_FILENAME}/nested.typ",
        f"lib/{INLINE_TEMPLATE_FILENAME}",
    ]

    for spelling in spellings:
        with pytest.raises(InvalidTemplatePathError):
            _ = validate_inline_file_key(spelling)


def test_validate_inline_file_key_accepts_main_typ_as_directory():
    """Harmless now: the entry point is not written to 'main.typ', so nothing collides with it."""
    assert validate_inline_file_key("main.typ/foo.typ") == "main.typ/foo.typ"


@pytest.mark.parametrize(
    "key",
    ["lib//utils.typ", "lib/./utils.typ", "lib/", "assets//logo.png"],
)
def test_validate_inline_file_key_rejects_non_normalised_keys(key: str):
    """Keys that are not their own normalised form would collide with the normalised spelling."""
    with pytest.raises(InvalidTemplatePathError):
        _ = validate_inline_file_key(key)


@pytest.mark.parametrize("key", ["Main.typ", "MAIN.TYP", "main.TYP", "Main.typ/foo.typ"])
def test_validate_inline_file_key_accepts_the_old_reserved_name_in_any_case(key: str):
    """
    These were rejected while the entry point was written to 'main.typ'.

    A case-insensitive volume made every spelling the same file as the entry point, so all of them
    had to go. The entry point now has a name no key can spell, so none of them collides with
    anything and all are ordinary keys.
    """
    assert validate_inline_file_key(key) == key


@pytest.mark.parametrize("key", ["plugins/hello.wasm", ".wasm", "lib/.wasm", "PLUGIN.WASM", "lib/mod.WaSm"])
def test_validate_inline_file_key_accepts_wasm_like_names(key: str):
    """
    The '.wasm' denial was removed because it denied nothing.

    Typst's plugin() identifies a module by its magic bytes, not by its name: a WebAssembly module
    named 'mod.dat' loads exactly as one named 'mod.wasm' does. The rule stopped only the honest
    spelling, while every caller had to mirror it — and mirror the detail that it compares the whole
    filename, because Path.suffix is empty for '.wasm' and 'lib/.wasm'. A published rule that costs
    callers real work and prevents nothing is worse than no rule. Pinned as an accepted case so the
    relaxation stays deliberate rather than becoming an omission nobody notices.
    """
    assert validate_inline_file_key(key) == key


def test_validate_inline_file_keys_rejects_a_key_that_is_a_prefix_of_another():
    """One key as a file and another as its directory cannot both exist; name both in the error."""
    with pytest.raises(InvalidFileDataError, match="lib"):
        _ = validate_inline_file_keys(["lib", "lib/utils.typ"])


def test_validate_inline_file_keys_rejects_the_prefix_in_either_order():
    with pytest.raises(InvalidFileDataError):
        _ = validate_inline_file_keys(["lib/utils.typ", "lib"])


def test_validate_inline_file_keys_rejects_keys_differing_only_in_case():
    with pytest.raises(InvalidFileDataError, match="case"):
        _ = validate_inline_file_keys(["lib/utils.typ", "lib/Utils.typ"])


def test_validate_inline_file_keys_accepts_siblings_and_nesting():
    keys = ["lib/utils.typ", "lib/table.typ", "assets/logo.png", "top.typ"]

    assert validate_inline_file_keys(keys) == keys


def test_validate_inline_file_key_rejects_an_oversized_segment():
    """A segment no filesystem will accept is a declared bound, not a platform probe."""
    with pytest.raises(InvalidTemplatePathError):
        _ = validate_inline_file_key("lib/" + "x" * 256)


def test_validate_inline_file_key_accepts_a_segment_at_the_bound():
    key = "lib/" + "x" * 255

    assert validate_inline_file_key(key) == key


def test_validate_inline_file_keys_caps_the_number_of_keys_before_validating_them():
    """
    The per-key work is O(keys x depth), so the count must be bounded before any of it runs.

    Asserted with keys that are individually invalid: getting the ceiling message rather than the
    per-key one is what proves the order.
    """
    keys = [f"../escape{index}.typ" for index in range(MAX_INLINE_FILE_KEYS + 1)]

    with pytest.raises(InvalidFileDataError, match="Too many"):
        _ = validate_inline_file_keys(keys)


def test_validate_inline_file_keys_accepts_the_ceiling():
    keys = [f"lib/file{index}.typ" for index in range(MAX_INLINE_FILE_KEYS)]

    assert len(validate_inline_file_keys(keys)) == MAX_INLINE_FILE_KEYS


def test_too_many_keys_reports_count_and_limit():
    keys = [f"f{i}.typ" for i in range(MAX_INLINE_FILE_KEYS + 1)]
    with pytest.raises(InvalidFileDataError) as exc_info:
        validate_inline_file_keys(keys)
    assert exc_info.value.context == {
        "count": MAX_INLINE_FILE_KEYS + 1,
        "limit": MAX_INLINE_FILE_KEYS,
        "rule": "too_many_keys",
    }


def test_case_collision_reports_the_later_key():
    with pytest.raises(InvalidFileDataError) as exc_info:
        validate_inline_file_keys(["Logo.png", "logo.png"])
    assert exc_info.value.context == {"key": "logo.png", "rule": "case_collision"}


def test_directory_collision_reports_the_nested_key():
    with pytest.raises(InvalidFileDataError) as exc_info:
        validate_inline_file_keys(["lib", "lib/x.typ"])
    assert exc_info.value.context == {"key": "lib/x.typ", "rule": "ancestor_collision"}


def test_invalid_template_path_publishes_no_key():
    """An invalid key has not been bounded by validation, so it is never echoed as a field."""
    with pytest.raises(InvalidTemplatePathError) as exc_info:
        validate_inline_file_key("/" + "a" * 5000)
    assert exc_info.value.context == {}


def _key_of_length(length: int) -> str:
    """A normalised key of exactly `length` characters, built from segments inside NAME_MAX."""
    segments = ["a" * 100] * (length // 101)
    key = "/".join(segments)
    return key + "/" + "a" * (length - len(key) - 1)


def test_a_key_at_the_length_limit_is_accepted():
    key = _key_of_length(MAX_KEY_LENGTH)
    assert validate_inline_file_key(key) == key


def test_a_key_past_the_length_limit_is_rejected():
    """Every segment is inside NAME_MAX, so only the whole-key bound can reject this."""
    with pytest.raises(InvalidTemplatePathError, match="longer than"):
        validate_inline_file_key(_key_of_length(MAX_KEY_LENGTH + 1))


def test_an_overlong_key_is_never_echoed_whole():
    key = _key_of_length(MAX_KEY_LENGTH + 1)
    with pytest.raises(InvalidTemplatePathError) as exc_info:
        validate_inline_file_key(key)
    assert key not in exc_info.value.detail
    assert exc_info.value.context == {}
