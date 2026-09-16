"""
The published statement of the output-option rules.

``app.models`` decides whether an ``output`` object is acceptable; this module only restates that
decision in a form a caller in another language can consume. Like ``app.core.constraints``, it must
never contain a rule of its own — a rule that lives here and not in ``models.py`` is a rule Prelum
does not enforce, and a caller that mirrors it refuses a request the service would have rendered.

Nothing here depends on a deployment: every value is a property of the code, so the whole document
is safe to write to a static file. The limits an operator can change are published by
``GET /v1/constraints`` under ``limits`` and are deliberately absent.
"""

from typing import Any, get_args

from app.models import (
    DEFAULT_PNG_PPI,
    IMAGE_OUTPUT_FORMATS,
    MAX_PAGE_SELECTION_LENGTH,
    MAX_PAGE_SELECTION_SEGMENTS,
    MAX_PDF_STANDARDS,
    MAX_PNG_PPI,
    MIN_IMAGE_PAGE,
    MIN_PNG_PPI,
    PAGE_SELECTION_PATTERN,
    PDF_A_4_STANDARDS,
    PDF_A_VERSION,
    PDF_OUTPUT_FORMATS,
    TAGGED_PDF_STANDARDS,
    ArchiveFormat,
    ImageOutputRules,
    OutputConformanceVector,
    OutputFormat,
    OutputRule,
    OutputRuleId,
    OutputRulesDocument,
    PageSelectionRules,
    PdfOutputRules,
    PdfStandard,
    PdfVersion,
    PngOutputRules,
)

# Rises whenever any published output rule changes. Separate from the files-key RULES_VERSION on
# purpose: the two contracts change at different rates, and one counter would force a caller to
# re-examine the key rules because a PDF standard was added, or the reverse.
OUTPUT_RULES_VERSION = 1

# The rules that need more than one field to decide, so the OpenAPI schema cannot express them. Each
# has a stable id, which the rejection carries in `context.rule`: every one of them answers
# `invalid_request` at the same `loc`, so without the id only the prose distinguished them.
#
# Listed in the order Prelum applies them, which `rule_evaluation` below states as data: an object
# breaking two rules reports the earlier one, and a mirror that checks them in another order
# disagrees with the service about which constraint the caller broke while agreeing that one was.
# The page-selection rules come first because `pages` is validated as a field, before any rule that
# needs a second field to decide.
RULES: list[dict[str, str]] = [
    {
        "id": OutputRuleId.page_selection_too_long.value,
        "error_code": "invalid_request",
        "description": (
            f"pages must not exceed max_length ({MAX_PAGE_SELECTION_LENGTH}) units of "
            "max_length_unit, counted before the selections are matched."
        ),
    },
    {
        "id": OutputRuleId.page_selection_too_many_segments.value,
        "error_code": "invalid_request",
        "description": (
            f"pages must not exceed max_selections ({MAX_PAGE_SELECTION_SEGMENTS}) comma-separated "
            "selections. Counted before the selections are matched, so a value breaking both this "
            "rule and selection_pattern reports this one."
        ),
    },
    {
        "id": OutputRuleId.page_selection_malformed.value,
        "error_code": "invalid_request",
        "description": (
            "Every comma-separated selection in pages must match selection_pattern in full. The "
            "grammar accepts ASCII digits only, matching Typst's own CLI."
        ),
    },
    {
        "id": OutputRuleId.page_range_end_precedes_start.value,
        "error_code": "invalid_request",
        "description": "A closed range in pages must not end before it starts.",
    },
    {
        "id": OutputRuleId.duplicate_standards.value,
        "error_code": "invalid_request",
        "description": "pdf.standards must not name the same standard twice.",
    },
    {
        "id": OutputRuleId.multiple_pdf_a_standards.value,
        "error_code": "invalid_request",
        "description": (
            "At most one PDF/A standard — one entry of pdf_a_version — may be selected. The second "
            "permitted entry is ua-1, which is not a PDF/A profile."
        ),
    },
    {
        "id": OutputRuleId.ua_1_with_pdf_a_4.value,
        "error_code": "invalid_request",
        "description": "ua-1 cannot be combined with a PDF/A-4 profile: a-4, a-4f or a-4e.",
    },
    {
        "id": OutputRuleId.version_conflicts_with_standard.value,
        "error_code": "invalid_request",
        "description": (
            "An explicit pdf.version must equal the version the selected PDF/A standard requires, "
            "as given by pdf_a_version. Omitting version is always accepted: Typst then uses the "
            "version the standard requires."
        ),
    },
    {
        "id": OutputRuleId.ua_1_with_pdf_2_0.value,
        "error_code": "invalid_request",
        "description": "ua-1 cannot be combined with an explicit pdf.version of 2.0.",
    },
    {
        "id": OutputRuleId.pages_with_tagged_standard.value,
        "error_code": "invalid_request",
        "description": (
            "pdf.pages cannot be combined with a standard that requires tagging — an entry of "
            "tagged_standards — because Typst disables tagging when pages are selected."
        ),
    },
    {
        "id": OutputRuleId.pages_requires_archive.value,
        "error_code": "invalid_request",
        "description": "An image output's pages requires archive: a single image file holds one page.",
    },
    {
        "id": OutputRuleId.page_with_archive.value,
        "error_code": "invalid_request",
        "description": "An image output's page cannot be combined with archive; select pages instead.",
    },
]

# Every output rule is demonstrated, so nothing is exempt. Kept rather than dropped because
# tests/test_output_rules.py requires a vector or a declared exemption for each id: a rule added
# without either fails there instead of shipping undemonstrated.
RULES_WITHOUT_VECTORS: frozenset[str] = frozenset()

# Executable rather than illustrative: tests/test_output_rules.py runs every one of them against the
# service, and callers are expected to run them against their own mirror. A vector without a `rule`
# is refused by a field-level rule the OpenAPI schema already expresses; it is still carried,
# because accept/reject is what catches drift.
CONFORMANCE_VECTORS: list[dict[str, Any]] = [
    {"output": {"format": "pdf"}, "accepted": True},
    {"output": {"format": "pdf", "version": "1.7", "standards": ["a-2b", "ua-1"]}, "accepted": True},
    {"output": {"format": "pdf", "version": "2.0", "standards": ["a-4f"]}, "accepted": True},
    # No explicit version, so nothing contradicts the standard's required one. A mirror that reads
    # version_conflicts_with_standard as "the standard fixes the version" rejects this and refuses
    # the request the service renders.
    {"output": {"format": "pdf", "standards": ["a-4"]}, "accepted": True},
    {"output": {"format": "pdf", "pages": "1,3-6,8-"}, "accepted": True},
    {"output": {"format": "png", "page": 2, "ppi": 300}, "accepted": True},
    {"output": {"format": "png", "archive": "zip", "pages": "1-2"}, "accepted": True},
    {"output": {"format": "svg", "archive": "zip"}, "accepted": True},
    {"output": {"format": "svg", "page": 1}, "accepted": True},
    {
        "output": {"format": "pdf", "standards": ["a-2b", "a-2b"]},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.duplicate_standards.value,
    },
    {
        "output": {"format": "pdf", "standards": ["a-2b", "a-3b"]},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.multiple_pdf_a_standards.value,
    },
    # Breaks two rules at once, which is what makes rule_evaluation falsifiable: a mirror applying
    # them in the other order reports the other rule while agreeing that the object is bad.
    {
        "output": {"format": "pdf", "version": "1.4", "standards": ["a-2b", "a-3b"]},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.multiple_pdf_a_standards.value,
    },
    {
        "output": {"format": "pdf", "standards": ["a-4", "ua-1"]},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.ua_1_with_pdf_a_4.value,
    },
    {
        "output": {"format": "pdf", "version": "1.4", "standards": ["a-2b"]},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.version_conflicts_with_standard.value,
    },
    {
        "output": {"format": "pdf", "version": "2.0", "standards": ["ua-1"]},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.ua_1_with_pdf_2_0.value,
    },
    {
        "output": {"format": "pdf", "standards": ["a-1a"], "pages": "1"},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.pages_with_tagged_standard.value,
    },
    {
        "output": {"format": "png", "pages": "1-2"},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.pages_requires_archive.value,
    },
    {
        "output": {"format": "png", "archive": "zip", "page": 1},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.page_with_archive.value,
    },
    {
        "output": {"format": "pdf", "pages": "1" + ",1" * MAX_PAGE_SELECTION_LENGTH},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.page_selection_too_long.value,
    },
    {
        "output": {"format": "pdf", "pages": ",".join(["1"] * (MAX_PAGE_SELECTION_SEGMENTS + 1))},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.page_selection_too_many_segments.value,
    },
    {
        "output": {"format": "pdf", "pages": "1-a"},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.page_selection_malformed.value,
    },
    # Selections are matched whole. A mirror that anchors selection_pattern with '^' and '$'
    # instead accepts this one, because in Python, PCRE and Java '$' also matches before a trailing
    # newline — the same mistake the files-key rules carry a vector for.
    {
        "output": {"format": "pdf", "pages": "1\n"},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.page_selection_malformed.value,
    },
    # 100 code points but 300 bytes, so it is inside max_length and fails the pattern instead. A
    # mirror measuring bytes reports page_selection_too_long here and never learns it disagrees,
    # because both answers reject the request.
    {
        # Written as an escape: the character is a fullwidth digit and is meant to be unmistakable.
        "output": {"format": "pdf", "pages": "\uff11" * 100},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.page_selection_malformed.value,
    },
    # Space after the comma. The grammar has no optional whitespace, and a mirror that trims each
    # selection before matching accepts a value the service refuses.
    {
        "output": {"format": "pdf", "pages": "1, 2"},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.page_selection_malformed.value,
    },
    {
        "output": {"format": "pdf", "pages": "3-2"},
        "accepted": False,
        "code": "invalid_request",
        "rule": OutputRuleId.page_range_end_precedes_start.value,
    },
    # Field-level rules from here on: the schema states each one, and the rejection carries no rule
    # id because Pydantic's own error type already tells them apart.
    {"output": {"format": "tiff"}, "accepted": False, "code": "unsupported_format"},
    {
        "output": {"format": "pdf", "standards": ["a-1b", "a-2b", "ua-1"]},
        "accepted": False,
        "code": "invalid_request",
    },
    {"output": {"format": "png", "ppi": MAX_PNG_PPI + 1}, "accepted": False, "code": "invalid_request"},
    {"output": {"format": "png", "ppi": MIN_PNG_PPI - 1}, "accepted": False, "code": "invalid_request"},
    # ppi is validated in strict mode, so a float is not coerced to the integer it happens to equal.
    {"output": {"format": "png", "ppi": 144.0}, "accepted": False, "code": "invalid_request"},
    {"output": {"format": "png", "page": MIN_IMAGE_PAGE - 1}, "accepted": False, "code": "invalid_request"},
    {"output": {"format": "png", "archive": "tar"}, "accepted": False, "code": "invalid_request"},
    # An option belonging to another format is an unknown field here, not an ignored one: output
    # objects are closed, because silently dropping ppi renders a different document with a 200.
    {"output": {"format": "svg", "ppi": DEFAULT_PNG_PPI}, "accepted": False, "code": "invalid_request"},
    {"output": {"format": "pdf", "archive": "zip"}, "accepted": False, "code": "invalid_request"},
]


# Validated into their models once at import rather than per call. The tables above stay literal
# data — that is the form a reader and a mirror both want — and a wrong key or type in one fails the
# import for every deployment and every test run, instead of on the first request that serves it.
_RULE_MODELS = [OutputRule.model_validate(rule) for rule in RULES]
_VECTOR_MODELS = [OutputConformanceVector.model_validate(vector) for vector in CONFORMANCE_VECTORS]


def output_rules() -> OutputRulesDocument:
    """Build the published output-option document, served and exported unchanged."""
    return OutputRulesDocument(
        output_rules_version=OUTPUT_RULES_VERSION,
        formats=[output_format.value for output_format in OutputFormat],
        pdf=PdfOutputRules(
            # Which formats each block governs, so that a mirror decides by reading rather than by
            # excluding — the same reason pdf_a_4_standards is published one level down.
            formats=sorted(output_format.value for output_format in PDF_OUTPUT_FORMATS),
            versions=[version.value for version in PdfVersion],
            standards=[standard.value for standard in PdfStandard],
            max_standards=MAX_PDF_STANDARDS,
            # The two tables a mirror cannot derive and would otherwise transcribe. pdf_a_version
            # also names the PDF/A profiles: a standard absent from it is not one, which is how
            # multiple_pdf_a_standards counts and how ua-1 is the one that may accompany a profile.
            pdf_a_version={standard.value: version.value for standard, version in PDF_A_VERSION.items()},
            tagged_standards=sorted(standard.value for standard in TAGGED_PDF_STANDARDS),
            # Published rather than left to prose so ua_1_with_pdf_a_4 can be applied from data: a
            # mirror inferring the family from the "a-4" prefix is parsing names, not reading rules.
            pdf_a_4_standards=sorted(standard.value for standard in PDF_A_4_STANDARDS),
        ),
        image=ImageOutputRules(
            formats=sorted(output_format.value for output_format in IMAGE_OUTPUT_FORMATS),
            archives=list(get_args(ArchiveFormat.__value__)),
            min_page=MIN_IMAGE_PAGE,
            png=PngOutputRules(min_ppi=MIN_PNG_PPI, max_ppi=MAX_PNG_PPI, default_ppi=DEFAULT_PNG_PPI),
        ),
        # Shared by pdf.pages and an image archive's pages: one parser answers both, so a mirror
        # needs this grammar whichever format it supports.
        page_selection=PageSelectionRules(
            max_length=MAX_PAGE_SELECTION_LENGTH,
            # The unit belongs beside the bound: max_length counts Unicode code points, not bytes,
            # and it is checked before the pattern is applied. A mirror in a language whose string
            # length is a byte count answers page_selection_too_long where the service answers
            # page_selection_malformed — both reject, so only the id reveals the disagreement. The
            # non-ASCII vector below is the one that catches it.
            max_length_unit="unicode code points",
            max_selections=MAX_PAGE_SELECTION_SEGMENTS,
            selection_pattern=PAGE_SELECTION_PATTERN,
            selection_pattern_flavour="pcre",
            # Stated as data because the expression cannot carry it: the pattern is applied to each
            # selection whole, and a mirror anchoring it with '^' and '$' accepts a trailing newline.
            selection_pattern_matches_whole_selection=True,
        ),
        rule_evaluation=(
            "rules are listed in the order Prelum applies them; the first to fail is the one reported in context.rule"
        ),
        rules=_RULE_MODELS,
        conformance_vectors=_VECTOR_MODELS,
    )
