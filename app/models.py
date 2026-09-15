import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, LiteralString, NoReturn, cast

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from app.core.errors import Origin

type JSONValue = str | int | float | bool | list[JSONValue] | dict[str, JSONValue] | Mapping[str, JSONValue] | None


class OutputFormat(StrEnum):
    pdf = "pdf"
    svg = "svg"
    png = "png"


class PdfVersion(StrEnum):
    v1_4 = "1.4"
    v1_5 = "1.5"
    v1_6 = "1.6"
    v1_7 = "1.7"
    v2_0 = "2.0"


class PdfStandard(StrEnum):
    a_1b = "a-1b"
    a_1a = "a-1a"
    a_2b = "a-2b"
    a_2u = "a-2u"
    a_2a = "a-2a"
    a_3b = "a-3b"
    a_3u = "a-3u"
    a_3a = "a-3a"
    a_4 = "a-4"
    a_4f = "a-4f"
    a_4e = "a-4e"
    ua_1 = "ua-1"


# A misspelt field must fail rather than be dropped: silently ignoring one renders a different
# document with a 200 response.
_STRICT = ConfigDict(extra="forbid", frozen=True)

# For the models that describe a response. They stay strict where they are validated — an undeclared
# key reaching one from inside this service is our bug, and failing there is how it stays visible —
# but the schema they publish must not repeat that promise to a caller. `extra="forbid"` exports
# `additionalProperties: false`, which says the body is final; a response may gain a field within the
# same API version, as `origin` did, and a client is expected to ignore what it does not recognise.
# The override states that openness in the published document without loosening validation here.
_STRICT_OPEN_SCHEMA = ConfigDict(extra="forbid", frozen=True, json_schema_extra={"additionalProperties": True})


class RenderFile(BaseModel):
    encoding: Literal["text", "base64"]
    content: str

    model_config: ClassVar[ConfigDict] = _STRICT


class Problem(BaseModel):
    """
    The `application/problem+json` body every error response carries.

    Documentation only: `AppError.to_response` builds the body itself, and this model exists so the
    OpenAPI export states the shape. Per-code `context` keys are listed in docs/api/errors.md.
    """

    code: str
    origin: Origin
    title: str
    status: int
    detail: str
    instance: str
    context: dict[str, object]

    model_config: ClassVar[ConfigDict] = _STRICT_OPEN_SCHEMA


class ConstraintSetRule(BaseModel):
    """One rule that applies to the complete set of auxiliary file keys."""

    id: str
    error_code: str
    description: str

    model_config: ClassVar[ConfigDict] = _STRICT_OPEN_SCHEMA


class ConstraintConformanceVector(BaseModel):
    """An executable accepted or rejected example for a files-key rules mirror."""

    keys: list[str]
    accepted: bool
    code: str | None = None
    rule: str | None = None

    model_config: ClassVar[ConfigDict] = _STRICT_OPEN_SCHEMA


class ConstraintLimits(BaseModel):
    """Deployment-specific limits published by the authenticated constraints endpoint."""

    effective_max_files: int
    max_inline_files: int
    max_inline_file_bytes: int
    max_template_source_bytes: int
    max_string_bytes: int
    max_request_body_bytes: int
    max_output_bytes: int
    max_output_files: int

    model_config: ClassVar[ConfigDict] = _STRICT_OPEN_SCHEMA


class OutputRule(BaseModel):
    """One output-option rule that needs more than one field to decide, named by its stable id."""

    id: str
    error_code: str
    description: str

    model_config: ClassVar[ConfigDict] = _STRICT_OPEN_SCHEMA


class OutputConformanceVector(BaseModel):
    """An executable accepted or rejected `output` object for an output-rules mirror."""

    output: dict[str, object]
    accepted: bool
    code: str | None = None
    # Absent where a field-level rule refused the object: the OpenAPI schema states those, and
    # Pydantic's own error type already tells them apart, so they carry no id of ours.
    rule: str | None = None

    model_config: ClassVar[ConfigDict] = _STRICT_OPEN_SCHEMA


class PngOutputRules(BaseModel):
    """The bounds PNG output applies to `ppi`, and the value it uses when none is given."""

    min_ppi: int
    max_ppi: int
    default_ppi: int

    model_config: ClassVar[ConfigDict] = _STRICT_OPEN_SCHEMA


class ImageOutputRules(BaseModel):
    """What PNG and SVG output accept beyond the fields every output shares."""

    # Which `format` values this block governs. Stated rather than left to be inferred from "not
    # pdf": a mirror deciding by exclusion applies these rules to any format added later.
    formats: list[str]
    archives: list[str]
    min_page: int
    png: PngOutputRules

    model_config: ClassVar[ConfigDict] = _STRICT_OPEN_SCHEMA


class PdfOutputRules(BaseModel):
    """The PDF vocabulary, with the two lookup tables a mirror would otherwise transcribe."""

    # Which `format` values this block governs, for the same reason as `image.formats`.
    formats: list[str]
    versions: list[str]
    standards: list[str]
    max_standards: int
    pdf_a_version: dict[str, str]
    tagged_standards: list[str]
    pdf_a_4_standards: list[str]

    model_config: ClassVar[ConfigDict] = _STRICT_OPEN_SCHEMA


class PageSelectionRules(BaseModel):
    """The `pages` grammar, shared by PDF output and an image archive."""

    max_length: int
    max_length_unit: str
    max_selections: int
    selection_pattern: str
    selection_pattern_flavour: Literal["pcre"]
    selection_pattern_matches_whole_selection: bool

    model_config: ClassVar[ConfigDict] = _STRICT_OPEN_SCHEMA


class OutputRulesDocument(BaseModel):
    """The deployment-independent output-option contract, also exported as output-rules.json."""

    output_rules_version: int
    formats: list[str]
    pdf: PdfOutputRules
    image: ImageOutputRules
    page_selection: PageSelectionRules
    rule_evaluation: str
    rules: list[OutputRule]
    conformance_vectors: list[OutputConformanceVector]

    model_config: ClassVar[ConfigDict] = _STRICT_OPEN_SCHEMA


class ConstraintsResponse(BaseModel):
    """The complete client contract returned by ``GET /v1/constraints``."""

    rules_version: int
    key_pattern: str
    key_pattern_flavour: Literal["pcre"]
    segment_character_class: str
    min_segment_length: int
    max_segment_length: int
    segments_may_not_be_only_dots: bool
    min_key_length: int
    max_key_length: int
    max_keys: int
    case_sensitivity: str
    set_rules: list[ConstraintSetRule]
    conformance_vectors: list[ConstraintConformanceVector]
    limits: ConstraintLimits
    # Nested rather than merged into this document's own fields, and carrying its own version
    # counter: the two rule sets change at different rates, so a shared `rules_version` would send
    # a caller back through the key rules because a PDF standard was added. One fetch still answers
    # both, which merging was the only other way to achieve.
    output_rules: OutputRulesDocument

    model_config: ClassVar[ConfigDict] = _STRICT_OPEN_SCHEMA


# Named so the published document can list the archive formats without a second spelling of "zip".
type ArchiveFormat = Literal["zip"]


class _RenderOutputBase(BaseModel):
    filename: str | None = None

    model_config: ClassVar[ConfigDict] = _STRICT


# Public because app/core/output_rules.py publishes each of them, and a limit with two spellings is
# a limit that can drift: the module that restates the rules must read the same object this module
# enforces them from. The one exception is the compiled expression below, which is the pattern's
# private form; the source string is what a caller in another language can use.

# Applied to one comma-separated selection with fullmatch, so it is anchored at both ends by the
# call rather than by the expression. A mirror that anchors with '^' and '$' instead accepts
# '1\n', which Python's fullmatch does not — the conformance vectors carry that case.
PAGE_SELECTION_PATTERN = r"[1-9][0-9]*(?:-(?:[1-9][0-9]*)?)?"
MAX_PAGE_SELECTION_LENGTH = 256
MAX_PAGE_SELECTION_SEGMENTS = 64
# Field-level bounds. They live here rather than inline in Field(...) for the same reason: the
# published document states each one, and a literal repeated in two files is the drift this whole
# artefact exists to prevent.
MAX_PDF_STANDARDS = 2
MIN_IMAGE_PAGE = 1
MIN_PNG_PPI = 1
MAX_PNG_PPI = 300
DEFAULT_PNG_PPI = 144

_PAGE_RANGE = re.compile(PAGE_SELECTION_PATTERN)
TAGGED_PDF_STANDARDS = {PdfStandard.a_1a, PdfStandard.a_2a, PdfStandard.a_3a, PdfStandard.ua_1}
PDF_A_VERSION = {
    PdfStandard.a_1b: PdfVersion.v1_4,
    PdfStandard.a_1a: PdfVersion.v1_4,
    PdfStandard.a_2b: PdfVersion.v1_7,
    PdfStandard.a_2u: PdfVersion.v1_7,
    PdfStandard.a_2a: PdfVersion.v1_7,
    PdfStandard.a_3b: PdfVersion.v1_7,
    PdfStandard.a_3u: PdfVersion.v1_7,
    PdfStandard.a_3a: PdfVersion.v1_7,
    PdfStandard.a_4: PdfVersion.v2_0,
    PdfStandard.a_4f: PdfVersion.v2_0,
    PdfStandard.a_4e: PdfVersion.v2_0,
}
# The PDF/A-4 family, derived from the map rather than spelled a second time. ua-1 is incompatible
# with exactly these three, and app/core/output_rules.py publishes the set so that a mirror can
# apply the rule from data instead of inferring the family from how a standard's name begins.
PDF_A_4_STANDARDS = frozenset(standard for standard in PDF_A_VERSION if standard.value.startswith("a-4"))


class OutputRuleId(StrEnum):
    """
    The published id of every output-option rule this module enforces.

    A rejection says only `invalid_request` at `body.output`, so until these ids existed the one
    thing telling a broken rule from its neighbour was `msg` — prose, which docs/api/errors.md
    reserves the right to reword, so a caller branching on it was transcribing our wording. The id
    travels in `context.rule`, and app/core/output_rules.py publishes the same ids beside the
    conformance vectors: a mirror can then assert *which* constraint fired, not merely that one did.
    """

    duplicate_standards = "duplicate_standards"
    multiple_pdf_a_standards = "multiple_pdf_a_standards"
    ua_1_with_pdf_a_4 = "ua_1_with_pdf_a_4"
    version_conflicts_with_standard = "version_conflicts_with_standard"
    ua_1_with_pdf_2_0 = "ua_1_with_pdf_2_0"
    pages_with_tagged_standard = "pages_with_tagged_standard"
    pages_requires_archive = "pages_requires_archive"
    page_with_archive = "page_with_archive"
    page_selection_too_long = "page_selection_too_long"
    page_selection_too_many_segments = "page_selection_too_many_segments"
    page_selection_malformed = "page_selection_malformed"
    page_range_end_precedes_start = "page_range_end_precedes_start"


def _reject(rule: OutputRuleId, message: str) -> NoReturn:
    """
    Fail validation with the id of the rule that fired carried beside its prose.

    The error type stays `value_error`, which is exactly what a bare ValueError raised from a
    validator produces: `errors[].type` is published, and an existing field changing meaning needs a
    new API version. The id therefore arrives as a new context key instead, which app/main.py lifts
    out of Pydantic's ctx onto the problem body.
    """
    raise PydanticCustomError("value_error", "{message}", {"message": message, "rule": rule.value})


@dataclass(frozen=True, slots=True)
class PageRange:
    start: int
    end: int | None


class PageSelectionLimitError(ValueError):
    count: int
    limit: int

    def __init__(self, *, count: int, limit: int) -> None:
        super().__init__(f"Page selection contains {count} pages; limit is {limit}")
        self.count = count
        self.limit = limit


def parse_page_selection(value: str) -> tuple[PageRange, ...]:
    if len(value) > MAX_PAGE_SELECTION_LENGTH:
        _reject(OutputRuleId.page_selection_too_long, f"pages must not exceed {MAX_PAGE_SELECTION_LENGTH} characters")

    selections = value.split(",")
    # Counted before the selections are matched, rather than beside them: the count is the cheap
    # half of what used to be one condition, and the two answers are now separate published rules,
    # so a value breaking both has to name the one that bounds the work the other would do.
    if len(selections) > MAX_PAGE_SELECTION_SEGMENTS:
        _reject(
            OutputRuleId.page_selection_too_many_segments,
            f"pages must not exceed {MAX_PAGE_SELECTION_SEGMENTS} comma-separated selections",
        )
    if any(_PAGE_RANGE.fullmatch(selection) is None for selection in selections):
        _reject(
            OutputRuleId.page_selection_malformed,
            "pages must be a comma-separated list of positive pages or ranges",
        )

    ranges: list[PageRange] = []
    for selection in selections:
        if "-" not in selection:
            page = int(selection)
            ranges.append(PageRange(page, page))
            continue

        start_text, end_text = selection.split("-", maxsplit=1)
        start = int(start_text)
        end = int(end_text) if end_text else None
        if end is not None and end < start:
            _reject(OutputRuleId.page_range_end_precedes_start, "page range end must not precede its start")
        ranges.append(PageRange(start, end))

    return tuple(ranges)


def _merge_page_ranges(ranges: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            previous_start, previous_end = merged[-1]
            merged[-1] = previous_start, max(previous_end, end)
        else:
            merged.append((start, end))
    return tuple(merged)


def bound_page_selection(value: str | None, *, limit: int) -> str:
    ranges = parse_page_selection(value) if value is not None else (PageRange(1, None),)
    open_starts = [page_range.start for page_range in ranges if page_range.end is None]
    open_start = min(open_starts, default=None)

    closed_ranges = [
        (page_range.start, page_range.end if open_start is None else min(page_range.end, open_start - 1))
        for page_range in ranges
        if page_range.end is not None and (open_start is None or page_range.start < open_start)
    ]
    merged = _merge_page_ranges(closed_ranges)
    closed_count = sum(end - start + 1 for start, end in merged)
    if closed_count > limit:
        raise PageSelectionLimitError(count=closed_count, limit=limit)

    pages = [page for start, end in merged for page in range(start, end + 1)]
    if open_start is not None:
        sentinel_end = open_start + (limit - closed_count)
        pages.extend(range(open_start, sentinel_end + 1))

    return ",".join(str(page) for page in pages)


def _validate_page_selection(value: str) -> str:
    _ = parse_page_selection(value)
    return value


type PageSelection = Annotated[str, AfterValidator(_validate_page_selection)]


class PdfOutput(_RenderOutputBase):
    format: Literal[OutputFormat.pdf] = OutputFormat.pdf
    version: PdfVersion | None = None
    standards: list[PdfStandard] = Field(default_factory=list, max_length=MAX_PDF_STANDARDS)
    pages: PageSelection | None = None

    @model_validator(mode="after")
    def validate_pdf_options(self) -> PdfOutput:
        if len(set(self.standards)) != len(self.standards):
            _reject(OutputRuleId.duplicate_standards, "PDF standards must not contain duplicates")

        pdf_a = [standard for standard in self.standards if standard in PDF_A_VERSION]
        if len(pdf_a) > 1:
            _reject(OutputRuleId.multiple_pdf_a_standards, "Only one PDF/A standard can be selected")
        if PdfStandard.ua_1 in self.standards and PDF_A_4_STANDARDS.intersection(pdf_a):
            _reject(OutputRuleId.ua_1_with_pdf_a_4, "PDF/UA-1 is incompatible with PDF/A-4")

        if self.version is not None and pdf_a and self.version != PDF_A_VERSION[pdf_a[0]]:
            _reject(
                OutputRuleId.version_conflicts_with_standard,
                f"{pdf_a[0].value} requires PDF version {PDF_A_VERSION[pdf_a[0]].value}",
            )
        if self.version == PdfVersion.v2_0 and PdfStandard.ua_1 in self.standards:
            _reject(OutputRuleId.ua_1_with_pdf_2_0, "PDF/UA-1 is incompatible with PDF 2.0")
        if self.pages is not None and TAGGED_PDF_STANDARDS.intersection(self.standards):
            _reject(
                OutputRuleId.pages_with_tagged_standard,
                "PDF page selection cannot be combined with a standard that requires tagging",
            )

        return self


class _ImageOutputBase(_RenderOutputBase):
    page: int | None = Field(default=None, ge=MIN_IMAGE_PAGE, strict=True)
    archive: ArchiveFormat | None = None
    pages: PageSelection | None = None

    @model_validator(mode="after")
    def validate_image_options(self) -> _ImageOutputBase:
        if self.archive is None and self.pages is not None:
            _reject(OutputRuleId.pages_requires_archive, "pages requires archive 'zip'")
        if self.archive is not None and self.page is not None:
            _reject(OutputRuleId.page_with_archive, "page cannot be combined with an archive")
        return self


class PngOutput(_ImageOutputBase):
    format: Literal[OutputFormat.png] = OutputFormat.png
    ppi: int = Field(default=DEFAULT_PNG_PPI, ge=MIN_PNG_PPI, le=MAX_PNG_PPI, strict=True)


class SvgOutput(_ImageOutputBase):
    format: Literal[OutputFormat.svg] = OutputFormat.svg


type RenderOutput = Annotated[PdfOutput | PngOutput | SvgOutput, Field(discriminator="format")]

# Which model validates each `format`, and so which rules apply to it. app/core/output_rules.py
# publishes both sets, because a mirror that reads "not pdf, therefore image" is deciding by
# exclusion: add a fourth format that is neither, and it silently applies the image rules to it.
# Derived from the class hierarchy rather than listed a second time, and every member of
# OutputFormat must be claimed by exactly one set — tests/test_output_rules.py asserts it.
OUTPUT_MODELS: dict[OutputFormat, type[_RenderOutputBase]] = {
    OutputFormat.pdf: PdfOutput,
    OutputFormat.png: PngOutput,
    OutputFormat.svg: SvgOutput,
}
PDF_OUTPUT_FORMATS = frozenset(
    output_format for output_format, model in OUTPUT_MODELS.items() if issubclass(model, PdfOutput)
)
IMAGE_OUTPUT_FORMATS = frozenset(
    output_format for output_format, model in OUTPUT_MODELS.items() if issubclass(model, _ImageOutputBase)
)


def _as_validation_error(check: Callable[[], object]) -> None:
    """
    Run a key-policy check, re-raising its failure as a Pydantic validation error.

    Preserves templates.py's caller-fault classification through Pydantic so the HTTP boundary can
    return the matching published problem code without reimplementing key policy. The error's
    context rides along in ctx; the handler lifts it out again.
    """
    from app.core.errors import InvalidFileDataError, InvalidTemplatePathError

    try:
        _ = check()
    except (InvalidTemplatePathError, InvalidFileDataError) as exc:
        error_type = cast(LiteralString, exc.code)
        raise PydanticCustomError(error_type, "{message}", {"message": str(exc), **exc.context}) from exc


class RenderRequest(BaseModel):
    source: str = Field(min_length=1)
    files: dict[str, RenderFile] = Field(default_factory=dict)
    data: JSONValue = Field(default_factory=dict)
    output: RenderOutput = Field(default_factory=PdfOutput)

    model_config: ClassVar[ConfigDict] = _STRICT

    @model_validator(mode="before")
    @classmethod
    def default_output_format(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        output = value.get("output")
        if not isinstance(output, dict) or "format" in output:
            return value
        return {**value, "output": {"format": OutputFormat.pdf, **output}}

    @model_validator(mode="before")
    @classmethod
    def bound_files_count(cls, value: object) -> object:
        """
        Refuse an oversized files mapping before a single entry is validated.

        Pydantic validates nested models before any mode="after" validator runs, so the key-count
        cap enforced there was reached only once every entry had produced its own errors — two per
        malformed entry, each one published. A mapping orders of magnitude past the cap turned a
        small body into a response many times its size, and spent the CPU to build it, all before
        the request competed for a render slot. Size is the one rule the raw mapping already answers.
        """
        from app.render.templates import ensure_key_count_fits

        if not isinstance(value, dict):
            return value
        files = cast(dict[str, object], value).get("files")
        if isinstance(files, dict):
            _as_validation_error(lambda: ensure_key_count_fits(len(cast(dict[str, object], files))))
        return value

    @model_validator(mode="after")
    def validate_file_keys(self) -> RenderRequest:
        from app.render.templates import validate_inline_file_keys

        _as_validation_error(lambda: validate_inline_file_keys(self.files))
        return self


@dataclass(frozen=True, slots=True)
class RenderJob:
    """Validated input consumed by the sole rendering path."""

    source: str
    files: Mapping[str, RenderFile]
    data: JSONValue
    output: RenderOutput
