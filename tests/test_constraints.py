"""The published rules document must agree with the code that enforces the rules.

A conformance vector that disagrees with the validator is worse than no vector: callers run these in
their own CI, so a wrong one propagates the drift it exists to prevent, wearing the authority of the
service.
"""

from typing import Any

import pytest

from app.core.constraints import (
    CONFORMANCE_VECTORS,
    RULES_VERSION,
    RULES_WITHOUT_VECTORS,
    SET_RULES,
    files_key_rules,
)
from app.core.errors import AppError
from app.render.templates import (
    MAX_INLINE_FILE_KEYS,
    MAX_KEY_LENGTH,
    MAX_KEY_SEGMENT_LENGTH,
    SAFE_SEGMENT_CHARACTER_CLASS,
    validate_inline_file_keys,
)


def _rejection_code(keys: list[str]) -> str | None:
    """Return the problem code the validator rejects these keys with, or None when it accepts them."""
    try:
        _ = validate_inline_file_keys(keys)
    except AppError as exc:
        return exc.code
    return None


@pytest.mark.parametrize("vector", CONFORMANCE_VECTORS, ids=lambda vector: "+".join(vector["keys"]) or "empty")
def test_every_conformance_vector_matches_the_validator(vector: dict[str, Any]):
    code = _rejection_code(vector["keys"])

    if vector["accepted"]:
        assert code is None, f"the validator rejected a vector marked accepted with '{code}'"
    else:
        assert code is not None, "the validator accepted a vector marked rejected"
        assert code == vector["code"]


def test_a_labelled_vector_is_rejected_by_the_rule_it_names():
    """
    The witness test below only checks that a label exists, not that it is true.

    Every set rule answers invalid_file_data, so asserting the code cannot tell them apart: a vector
    labelled case_collision that in fact trips the ancestor rule would satisfy both tests while
    leaving case_collision without the witness it is credited with.
    """
    for vector in CONFORMANCE_VECTORS:
        if "rule" not in vector:
            continue
        with pytest.raises(AppError) as exc_info:
            _ = validate_inline_file_keys(vector["keys"])
        assert exc_info.value.context["rule"] == vector["rule"], vector["keys"]


def test_every_set_rule_has_a_vector_or_a_declared_exemption():
    covered = {vector["rule"] for vector in CONFORMANCE_VECTORS if "rule" in vector}

    for rule in SET_RULES:
        assert rule["id"] in covered or rule["id"] in RULES_WITHOUT_VECTORS, (
            f"set rule {rule['id']} has neither a conformance vector nor a declared exemption"
        )


def test_declared_exemptions_name_real_rules():
    unknown = RULES_WITHOUT_VECTORS - {rule["id"] for rule in SET_RULES}

    assert not unknown, f"RULES_WITHOUT_VECTORS exempts rules that do not exist: {sorted(unknown)}"


def test_document_states_the_enforced_limits():
    document = files_key_rules().published()

    assert document["rules_version"] == RULES_VERSION
    assert document["max_segment_length"] == MAX_KEY_SEGMENT_LENGTH
    assert document["max_key_length"] == MAX_KEY_LENGTH
    assert document["max_keys"] == MAX_INLINE_FILE_KEYS
    assert document["segment_character_class"] == SAFE_SEGMENT_CHARACTER_CLASS


def test_every_per_key_rule_is_published_without_the_pattern():
    """
    A caller whose engine cannot compile key_pattern must still be able to apply every per-key rule.

    Go's regexp and Rust's regex are RE2: they have no lookahead, so the expression does not compile
    at all and the rules it carries have to be applied as code. That is only possible when each one
    is also published as data, which is what this asserts — the pattern is a fast path for the
    engines that accept it, never the sole statement of a rule.
    """
    document = files_key_rules().published()

    assert {
        "segment_character_class",
        "min_segment_length",
        "max_segment_length",
        "segments_may_not_be_only_dots",
        "min_key_length",
        "max_key_length",
    } <= document.keys()


def test_no_name_is_published_as_reserved():
    """The entry point is protected by being unspellable, so there is no reserved name to publish."""
    assert "reserved_first_segment" not in files_key_rules().published()


def test_static_half_carries_no_deployment_configuration():
    """Limits belong to the endpoint: a default in the artefact invites the caller to hardcode it."""
    document = files_key_rules().published()

    assert "limits" not in document
    assert not {"font_path", "local_package_path", "cli_path", "environment"} & document.keys()
