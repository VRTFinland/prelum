import pytest

from app.core.errors import InvalidFileDataError, InvalidTemplatePathError
from app.render.templates import (
    MAX_INLINE_FILE_KEYS,
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


def test_validate_inline_file_key_rejects_reserved_main_typ():
    with pytest.raises(InvalidTemplatePathError):
        _ = validate_inline_file_key("main.typ")


def test_validate_inline_file_key_rejects_reserved_main_typ_with_dot_prefix():
    with pytest.raises(InvalidTemplatePathError):
        _ = validate_inline_file_key("./main.typ")


def test_validate_inline_file_key_accepts_main_typ_in_subdirectory():
    assert validate_inline_file_key("lib/main.typ") == "lib/main.typ"


def test_validate_inline_file_key_rejects_wasm():
    with pytest.raises(InvalidTemplatePathError):
        _ = validate_inline_file_key("plugins/hello.wasm")


def test_validate_inline_file_key_rejects_wasm_case_insensitively():
    with pytest.raises(InvalidTemplatePathError):
        _ = validate_inline_file_key("plugins/hello.WASM")


def test_validate_inline_file_key_rejects_main_typ_as_directory():
    """'main.typ' as a directory would make the renderer write the entry point over a directory."""
    with pytest.raises(InvalidTemplatePathError):
        _ = validate_inline_file_key("main.typ/foo.typ")


@pytest.mark.parametrize(
    "key",
    ["lib//utils.typ", "lib/./utils.typ", "lib/", "assets//logo.png"],
)
def test_validate_inline_file_key_rejects_non_normalised_keys(key: str):
    """Keys that are not their own normalised form would collide with the normalised spelling."""
    with pytest.raises(InvalidTemplatePathError):
        _ = validate_inline_file_key(key)


@pytest.mark.parametrize("key", ["Main.typ", "MAIN.TYP", "main.TYP", "Main.typ/foo.typ"])
def test_validate_inline_file_key_rejects_reserved_name_in_any_case(key: str):
    """Case-insensitive volumes would let these overwrite the inline entry point."""
    with pytest.raises(InvalidTemplatePathError):
        _ = validate_inline_file_key(key)


@pytest.mark.parametrize("key", [".wasm", "lib/.wasm", "PLUGIN.WASM", "lib/mod.WaSm"])
def test_validate_inline_file_key_rejects_wasm_in_any_spelling(key: str):
    """Path.suffix is empty for dotfiles, so a suffix comparison alone misses '.wasm'."""
    with pytest.raises(InvalidTemplatePathError):
        _ = validate_inline_file_key(key)


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
