"""
The published statement of the files-key rules.

``app.render.templates`` decides whether a key is acceptable; this module only restates that
decision in a form a caller in another language can consume. It must never contain a rule of its
own — a rule that lives here and not in templates.py is a rule Prelum does not enforce, and a caller
that mirrors it rejects keys the service accepts.
"""

from typing import Any

from app.core.config import Settings
from app.core.output_rules import output_rules
from app.models import (
    ConstraintConformanceVector,
    ConstraintLimits,
    ConstraintSetRule,
    ConstraintsResponse,
    FilesKeyRules,
)
from app.render.templates import (
    INLINE_FILE_KEY_PATTERN,
    MAX_INLINE_FILE_KEYS,
    MAX_KEY_LENGTH,
    MAX_KEY_SEGMENT_LENGTH,
    SAFE_SEGMENT_CHARACTER_CLASS,
    effective_key_limit,
)

# Rises whenever any published rule changes, so a caller can diff its mirror against a known version
# rather than against prose. It is not the service version: the rules can outlast several releases.
RULES_VERSION = 5

# The rules a single key cannot answer. They resist expression as data, so they are named, described
# and — apart from the declared exemption below — demonstrated by a conformance vector.
SET_RULES: list[dict[str, str]] = [
    {
        "id": "too_many_keys",
        "error_code": "invalid_file_data",
        "description": (
            "At most max_keys entries. Checked before any other whole-set rule, because those are O(keys x depth)."
        ),
    },
    {
        "id": "case_collision",
        "error_code": "invalid_file_data",
        "description": "No two keys whose case-folded forms are equal.",
    },
    {
        "id": "ancestor_collision",
        "error_code": "invalid_file_data",
        "description": (
            "No key whose case-folded form equals a case-folded POSIX ancestor of another key: one "
            "entry cannot be both a file and a directory."
        ),
    },
]

# A vector for too_many_keys would need max_keys + 1 entries and would dominate the artefact, while
# the rule is a plain count against a limit already published as data. Declared rather than silently
# absent, so a new set rule without a vector still fails tests/test_constraints.py.
RULES_WITHOUT_VECTORS = frozenset({"too_many_keys"})

# Executable rather than illustrative: tests/test_constraints.py runs each one against
# validate_inline_file_keys, and callers are expected to run them against their own mirror.
CONFORMANCE_VECTORS: list[dict[str, Any]] = [
    {"keys": ["lib/label.typ"], "accepted": True},
    {"keys": ["assets/logo.png", "lib/nested/deep.typ"], "accepted": True},
    {"keys": ["main"], "accepted": True},
    {"keys": ["lib/main.typ"], "accepted": True},
    {"keys": [""], "accepted": False, "code": "invalid_template_path"},
    {"keys": ["/etc/passwd"], "accepted": False, "code": "invalid_template_path"},
    {"keys": ["../etc/passwd"], "accepted": False, "code": "invalid_template_path"},
    {"keys": ["./lib/label.typ"], "accepted": False, "code": "invalid_template_path"},
    {"keys": ["lib//label.typ"], "accepted": False, "code": "invalid_template_path"},
    {"keys": ["lib/"], "accepted": False, "code": "invalid_template_path"},
    {"keys": ["lib/..."], "accepted": False, "code": "invalid_template_path"},
    {"keys": ["lib/a b.typ"], "accepted": False, "code": "invalid_template_path"},
    {"keys": ["lib/back\\slash.typ"], "accepted": False, "code": "invalid_template_path"},
    # A mirror that anchors key_pattern with '$' rather than '\\z' accepts this one, because in
    # Python, PCRE and Java '$' also matches before a trailing newline. Kept as a vector because it
    # is the mistake a caller is most likely to make while believing it mirrored the pattern.
    {"keys": ["lib/label.typ\n"], "accepted": False, "code": "invalid_template_path"},
    # The same mistake at the other end: a mirror anchored with '^' accepts this one wherever '^'
    # means start-of-line, which in Ruby it always does. The match begins after the newline and the
    # rest of the key is valid, so nothing else in the expression notices.
    {"keys": ["bad\nlib/label.typ"], "accepted": False, "code": "invalid_template_path"},
    {
        "keys": [f"{'x' * (MAX_KEY_SEGMENT_LENGTH + 1)}.typ"],
        "accepted": False,
        "code": "invalid_template_path",
    },
    # Every segment is inside max_segment_length, so only the whole-key bound rejects this. A mirror
    # that applies the per-segment rules alone accepts it and learns the difference from a 400.
    {
        "keys": ["/".join(["a" * 100] * 7) + "/" + "a" * 62],
        "accepted": False,
        "code": "invalid_template_path",
    },
    # No name is reserved. The inline source is written to a name holding a character outside
    # segment_character_class, so no key can spell it and these are ordinary keys. Kept as accepted
    # vectors because a mirror written against an earlier draft rejects them.
    {"keys": ["main.typ"], "accepted": True},
    {"keys": ["Main.TYP/x.typ"], "accepted": True},
    {
        "keys": ["a/b.typ", "A/B.TYP"],
        "accepted": False,
        "code": "invalid_file_data",
        "rule": "case_collision",
    },
    {
        "keys": ["lib", "LIB/x.typ"],
        "accepted": False,
        "code": "invalid_file_data",
        "rule": "ancestor_collision",
    },
]


# Validated into their models once at import, for the reason output_rules.py states: the tables stay
# literal data, and a wrong key or type in one fails the import rather than the first request.
_SET_RULE_MODELS = [ConstraintSetRule.model_validate(rule) for rule in SET_RULES]
_VECTOR_MODELS = [ConstraintConformanceVector.model_validate(vector) for vector in CONFORMANCE_VECTORS]


def files_key_rules() -> FilesKeyRules:
    """
    Build the deployment-independent half of the published document.

    Everything here is a property of the code rather than of the deployment, so it is safe to write
    to a static file. The limits an operator can change are added by ``build_constraints``.
    """
    return FilesKeyRules(
        rules_version=RULES_VERSION,
        key_pattern=INLINE_FILE_KEY_PATTERN,
        key_pattern_flavour="pcre",
        # key_pattern needs lookahead, which RE2 — Go's regexp and Rust's regex — does not have, so
        # it does not compile there at all. Every rule the expression carries is therefore also
        # published on its own, and the fields below are the whole per-key contract: the class, the
        # two segment bounds, the dot-only flag and the two key bounds, applied to the segments of a
        # key split on '/'. The expression is a fast path for the engines that accept it, never a
        # rule's only published form.
        # tests/test_inline_file_key_properties.py mirrors the validator from these fields alone, so
        # a rule that reaches templates.py without reaching this dict fails there rather than in a
        # caller's deployment.
        segment_character_class=SAFE_SEGMENT_CHARACTER_CLASS,
        # A minimum of one is a rule, not a truism: keys are mirrored by splitting on '/', and an
        # empty segment is what 'lib//label.typ', 'lib/' and '/lib/x.typ' each produce. A mirror
        # that only checks "every character is in the class" passes all three vacuously.
        min_segment_length=1,
        max_segment_length=MAX_KEY_SEGMENT_LENGTH,
        # The one per-key rule with no parameter to publish: a segment of nothing but dots — '.',
        # '..', '...' — is rejected whatever its length. A flag rather than prose, so a mirror built
        # from data alone can see it; relaxing the rule would flip this rather than silently drop it.
        segments_may_not_be_only_dots=True,
        min_key_length=1,
        # Bounds the joined key, not a segment: what reaches the filesystem is the absolute project
        # root plus the key, and the room reserved for that root is what this leaves.
        max_key_length=MAX_KEY_LENGTH,
        max_keys=MAX_INLINE_FILE_KEYS,
        case_sensitivity="keys are compared case-folded",
        set_rules=_SET_RULE_MODELS,
        conformance_vectors=_VECTOR_MODELS,
    )


def build_constraints(settings: Settings) -> ConstraintsResponse:
    """
    Build the full document served by ``GET /v1/constraints``.

    ``max_keys`` is structural and ``max_inline_files`` is configurable, so the effective cap is the
    smaller of the two. It is stated outright because a caller that mirrors only one of them has the
    contract wrong in one direction or the other.

    The output rules are nested whole rather than merged, so their version counter stays their own;
    everything a caller needs to validate a request before dispatch then arrives in one fetch.
    """
    # The static half is declared once, in files_key_rules; this widens it rather than restating
    # any of its fields. vars() hands over the already-validated values, nested models included,
    # so nothing is serialised and re-parsed on the way.
    return ConstraintsResponse(
        **vars(files_key_rules()),
        output_rules=output_rules(),
        limits=ConstraintLimits(
            effective_max_files=effective_key_limit(settings.max_inline_files),
            max_inline_files=settings.max_inline_files,
            max_inline_file_bytes=settings.max_inline_file_bytes,
            max_template_source_bytes=settings.max_template_source_bytes,
            max_string_bytes=settings.max_string_bytes,
            max_request_body_bytes=settings.max_request_body_bytes,
            max_output_bytes=settings.max_output_bytes,
            max_output_files=settings.max_output_files,
        ),
    )
