import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, LiteralString, cast

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

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


class RenderFile(BaseModel):
    encoding: Literal["text", "base64"]
    content: str

    model_config: ClassVar[ConfigDict] = _STRICT


class _RenderOutputBase(BaseModel):
    filename: str | None = None

    model_config: ClassVar[ConfigDict] = _STRICT


_PAGE_RANGE = re.compile(r"[1-9][0-9]*(?:-(?:[1-9][0-9]*)?)?")
_MAX_PAGE_SELECTION_LENGTH = 256
_MAX_PAGE_SELECTION_SEGMENTS = 64
_TAGGED_PDF_STANDARDS = {PdfStandard.a_1a, PdfStandard.a_2a, PdfStandard.a_3a, PdfStandard.ua_1}
_PDF_A_VERSION = {
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
    if len(value) > _MAX_PAGE_SELECTION_LENGTH:
        raise ValueError(f"pages must not exceed {_MAX_PAGE_SELECTION_LENGTH} characters")

    selections = value.split(",")
    if len(selections) > _MAX_PAGE_SELECTION_SEGMENTS or any(
        _PAGE_RANGE.fullmatch(selection) is None for selection in selections
    ):
        raise ValueError("pages must be a comma-separated list of positive pages or ranges")

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
            raise ValueError("page range end must not precede its start")
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
    standards: list[PdfStandard] = Field(default_factory=list, max_length=2)
    pages: PageSelection | None = None

    @model_validator(mode="after")
    def validate_pdf_options(self) -> PdfOutput:
        if len(set(self.standards)) != len(self.standards):
            raise ValueError("PDF standards must not contain duplicates")

        pdf_a = [standard for standard in self.standards if standard in _PDF_A_VERSION]
        if len(pdf_a) > 1:
            raise ValueError("Only one PDF/A standard can be selected")
        if PdfStandard.ua_1 in self.standards and any(standard.value.startswith("a-4") for standard in pdf_a):
            raise ValueError("PDF/UA-1 is incompatible with PDF/A-4")

        if self.version is not None and pdf_a and self.version != _PDF_A_VERSION[pdf_a[0]]:
            raise ValueError(f"{pdf_a[0].value} requires PDF version {_PDF_A_VERSION[pdf_a[0]].value}")
        if self.version == PdfVersion.v2_0 and PdfStandard.ua_1 in self.standards:
            raise ValueError("PDF/UA-1 is incompatible with PDF 2.0")
        if self.pages is not None and _TAGGED_PDF_STANDARDS.intersection(self.standards):
            raise ValueError("PDF page selection cannot be combined with a standard that requires tagging")

        return self


class _ImageOutputBase(_RenderOutputBase):
    page: int | None = Field(default=None, ge=1, strict=True)
    archive: Literal["zip"] | None = None
    pages: PageSelection | None = None

    @model_validator(mode="after")
    def validate_image_options(self) -> _ImageOutputBase:
        if self.archive is None and self.pages is not None:
            raise ValueError("pages requires archive 'zip'")
        if self.archive is not None and self.page is not None:
            raise ValueError("page cannot be combined with an archive")
        return self


class PngOutput(_ImageOutputBase):
    format: Literal[OutputFormat.png] = OutputFormat.png
    ppi: int = Field(default=144, ge=1, le=300, strict=True)


class SvgOutput(_ImageOutputBase):
    format: Literal[OutputFormat.svg] = OutputFormat.svg


type RenderOutput = Annotated[PdfOutput | PngOutput | SvgOutput, Field(discriminator="format")]


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

    @model_validator(mode="after")
    def validate_file_keys(self) -> RenderRequest:
        from app.core.errors import InvalidFileDataError, InvalidTemplatePathError
        from app.render.templates import validate_inline_file_keys

        try:
            _ = validate_inline_file_keys(self.files)
        except (InvalidTemplatePathError, InvalidFileDataError) as exc:
            # Preserve templates.py's caller-fault classification through Pydantic so the HTTP
            # boundary can return the matching published problem code without reimplementing key policy.
            error_type = cast(LiteralString, exc.code)
            raise PydanticCustomError(error_type, "{message}", {"message": str(exc)}) from exc

        return self


@dataclass(frozen=True, slots=True)
class RenderJob:
    """Validated input consumed by the sole rendering path."""

    source: str
    files: Mapping[str, RenderFile]
    data: JSONValue
    output: RenderOutput
