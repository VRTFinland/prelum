"""Every error response carries a stable machine-readable code.

Status alone cannot identify a failure: several distinct errors share 413, and a caller that wants to
tell "your image is too big" from "the rendered PDF is too big" has nothing to branch on. The code
is the contract callers key on; the title is for humans and may be reworded freely.
"""

import inspect
import json

import pytest

from app.core import errors as errors_module
from app.core.errors import (
    AppError,
    ForbiddenError,
    InlineTemplateError,
    InvalidFileDataError,
    InvalidRequestError,
    InvalidTemplatePathError,
    MethodNotAllowedError,
    NotFoundError,
    Origin,
    OutputTooLargeError,
    PageSelectionTooLargeError,
    RenderError,
    RenderTimeoutError,
    RequestTooLargeError,
    ServiceOverloadedError,
    ServiceUnavailableError,
    StringTooLargeError,
    TemplateFileTooLargeError,
    TemplateSourceTooLargeError,
    TooManyOutputFilesError,
    UnsupportedFormatError,
)


def _error_classes() -> list[type[AppError]]:
    return [
        obj
        for _, obj in inspect.getmembers(errors_module, inspect.isclass)
        if issubclass(obj, AppError) and obj is not AppError
    ]


def test_every_error_class_declares_a_code():
    missing = [cls.__name__ for cls in _error_classes() if not getattr(cls, "code", "")]
    assert not missing, f"error classes without a code: {missing}"


def test_codes_are_unique():
    """A duplicate code would silently merge two failures the caller needs to tell apart."""
    codes = [cls.code for cls in _error_classes()]
    duplicates = {code for code in codes if codes.count(code) > 1}
    assert not duplicates, f"duplicate codes: {duplicates}"


def test_codes_are_snake_case_tokens():
    """Callers embed these in switch statements and translation keys, so the shape is part of the contract."""
    for cls in _error_classes():
        assert cls.code.replace("_", "").isalnum(), f"{cls.__name__}: {cls.code!r}"
        assert cls.code == cls.code.lower(), f"{cls.__name__}: {cls.code!r}"
        assert not cls.code.startswith("_"), f"{cls.__name__}: {cls.code!r}"
        assert not cls.code.endswith("_"), f"{cls.__name__}: {cls.code!r}"


@pytest.mark.parametrize(
    ("error_class", "expected"),
    [
        (ServiceOverloadedError, "render_queue_full"),
        (RenderTimeoutError, "render_timeout"),
        (InlineTemplateError, "template_compile_failed"),
        (InvalidRequestError, "invalid_request"),
        (InvalidTemplatePathError, "invalid_template_path"),
        (InvalidFileDataError, "invalid_file_data"),
        (UnsupportedFormatError, "unsupported_format"),
        (StringTooLargeError, "string_too_large"),
        (RequestTooLargeError, "request_too_large"),
        (TemplateSourceTooLargeError, "template_source_too_large"),
        (TemplateFileTooLargeError, "template_file_too_large"),
        (OutputTooLargeError, "output_too_large"),
        (PageSelectionTooLargeError, "page_selection_too_large"),
        (TooManyOutputFilesError, "too_many_output_files"),
        (ForbiddenError, "forbidden"),
        (RenderError, "render_failed"),
        (ServiceUnavailableError, "service_unavailable"),
    ],
)
def test_code_values_are_pinned(error_class: type[AppError], expected: str):
    """These strings are a published contract: changing one breaks every caller branching on it."""
    assert error_class.code == expected


def test_the_413_family_is_distinguishable_by_code():
    """The reason codes exist: different failures answer 413, and only the code separates them."""
    family = [
        StringTooLargeError,
        RequestTooLargeError,
        TemplateSourceTooLargeError,
        TemplateFileTooLargeError,
        OutputTooLargeError,
        PageSelectionTooLargeError,
        TooManyOutputFilesError,
    ]

    assert {cls.status for cls in family} == {413}
    assert len({cls.code for cls in family}) == len(family)


# One instance per error class, with the exact context key set it publishes. The vocabulary is a
# contract like the codes: a new key here is a deliberate change to docs/api/errors.md.
_CONTEXT_CONTRACT: list[tuple[AppError, frozenset[str]]] = [
    (InvalidRequestError("bad"), frozenset()),
    (UnsupportedFormatError("bad"), frozenset()),
    (InvalidTemplatePathError("bad"), frozenset()),
    (InvalidFileDataError("bad"), frozenset()),
    (InvalidFileDataError("bad", context={"key": "a.png"}), frozenset({"key"})),
    (InvalidFileDataError("bad", context={"count": 2, "limit": 1}), frozenset({"count", "limit"})),
    (
        InvalidFileDataError("bad", context={"key": "a.png", "rule": "case_collision"}),
        frozenset({"key", "rule"}),
    ),
    (ForbiddenError("bad"), frozenset()),
    (NotFoundError("bad"), frozenset()),
    (MethodNotAllowedError("bad"), frozenset()),
    (RenderTimeoutError(timeout_secs=15), frozenset({"timeout_secs"})),
    (RequestTooLargeError(limit=1), frozenset({"limit"})),
    (RequestTooLargeError(limit=1, declared_size=2), frozenset({"limit", "declared_size"})),
    (TemplateSourceTooLargeError(size=2, limit=1), frozenset({"size", "limit"})),
    (TemplateFileTooLargeError(key="a.png", size=2, limit=1), frozenset({"key", "size", "limit"})),
    (StringTooLargeError(size=2, limit=1), frozenset({"path", "subject", "size", "limit"})),
    (OutputTooLargeError(size=2, limit=1), frozenset({"size", "limit"})),
    (PageSelectionTooLargeError(count=2, limit=1), frozenset({"count", "limit"})),
    (TooManyOutputFilesError(limit=1), frozenset({"limit"})),
    (InlineTemplateError("bad"), frozenset()),
    (ServiceOverloadedError(retry_after=3), frozenset({"retry_after"})),
    (RenderError("bad"), frozenset()),
    (ServiceUnavailableError("bad"), frozenset()),
]

_STANDARD_MEMBERS = {"code", "title", "status", "detail", "instance", "context"}


@pytest.mark.parametrize(("error", "keys"), _CONTEXT_CONTRACT, ids=lambda item: getattr(item, "code", None))
def test_context_key_sets_are_pinned(error: AppError, keys: frozenset[str]):
    assert frozenset(error.context) == keys


def test_every_error_class_appears_in_the_context_contract():
    covered = {type(error) for error, _ in _CONTEXT_CONTRACT}
    missing = [cls.__name__ for cls in _error_classes() if cls not in covered]
    assert not missing, f"error classes without a context contract: {missing}"


@pytest.mark.parametrize(("error", "_keys"), _CONTEXT_CONTRACT, ids=lambda item: getattr(item, "code", None))
def test_context_never_shadows_a_standard_member_and_always_serialises(error: AppError, _keys: frozenset[str]):
    assert not (set(error.context) & _STANDARD_MEMBERS)
    assert json.loads(json.dumps(dict(error.context))) == dict(error.context)


@pytest.mark.parametrize(("error", "_keys"), _CONTEXT_CONTRACT, ids=lambda item: getattr(item, "code", None))
def test_detail_is_always_prose(error: AppError, _keys: frozenset[str]):
    assert isinstance(error.detail, str)
    assert error.detail


def test_every_error_class_declares_its_own_origin():
    """An inherited origin is how the caller's allowlist drifted; a new class must state its own."""
    inheriting = [cls.__name__ for cls in _error_classes() if "origin" not in cls.__dict__]
    assert not inheriting, f"error classes inheriting origin instead of declaring it: {inheriting}"


def test_origins_are_members_of_the_published_enum():
    for cls in _error_classes():
        assert isinstance(cls.origin, Origin), f"{cls.__name__}: {cls.origin!r}"


def test_is_server_fault_follows_origin_and_nothing_else():
    """One classification drives the published field and the log level, so they cannot disagree."""
    for cls in _error_classes():
        # cls.__new__(cls) rather than object.__new__(cls): BaseException defines its own __new__,
        # and calling object's directly is rejected on this interpreter. Neither runs __init__, so
        # this still checks the class-level declaration without satisfying each subclass's kwargs.
        error = cls.__new__(cls)
        assert error.is_server_fault is (cls.origin is Origin.service), cls.__name__


def test_every_server_status_is_attributed_to_the_service():
    """A 5xx may never claim to be the caller's doing.

    Deliberately one-way: the converse is not asserted, so a future 4xx that is genuinely ours stays
    expressible and status-derived classification cannot creep back in.
    """
    misattributed = [cls.__name__ for cls in _error_classes() if cls.status >= 500 and cls.origin is not Origin.service]
    assert not misattributed, f"5xx classes not attributed to the service: {misattributed}"


@pytest.mark.parametrize(
    ("error_class", "expected"),
    [
        (InvalidRequestError, Origin.request),
        (UnsupportedFormatError, Origin.request),
        (InvalidTemplatePathError, Origin.request),
        (InvalidFileDataError, Origin.request),
        (ForbiddenError, Origin.request),
        (NotFoundError, Origin.request),
        (MethodNotAllowedError, Origin.request),
        (RenderTimeoutError, Origin.request),
        (StringTooLargeError, Origin.request),
        (RequestTooLargeError, Origin.request),
        (OutputTooLargeError, Origin.request),
        (PageSelectionTooLargeError, Origin.request),
        (TooManyOutputFilesError, Origin.request),
        (TemplateSourceTooLargeError, Origin.template),
        (TemplateFileTooLargeError, Origin.template),
        (InlineTemplateError, Origin.template),
        (ServiceOverloadedError, Origin.capacity),
        (RenderError, Origin.service),
        (ServiceUnavailableError, Origin.service),
    ],
)
def test_origin_values_are_pinned(error_class: type[AppError], expected: Origin):
    assert error_class.origin is expected


def test_the_413_family_is_split_by_origin():
    """The reason the field exists: seven codes answer 413 and only some are the caller's doing."""
    assert TemplateSourceTooLargeError.origin is Origin.template
    assert TemplateFileTooLargeError.origin is Origin.template
    assert StringTooLargeError.origin is Origin.request
    assert RequestTooLargeError.origin is Origin.request
    assert OutputTooLargeError.origin is Origin.request
    assert PageSelectionTooLargeError.origin is Origin.request
    assert TooManyOutputFilesError.origin is Origin.request
