"""Properties for the page selector shared by PDF and image archive output."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.models import PageSelectionLimitError, bound_page_selection, parse_page_selection


def _pages(value: str) -> set[int]:
    return {int(page) for page in value.split(",")}


def test_bounded_selection_defaults_to_all_pages_plus_a_sentinel():
    assert bound_page_selection(None, limit=3) == "1,2,3,4"


def test_bounded_selection_normalises_duplicates_and_overlaps():
    assert bound_page_selection("1-3,2,3-4", limit=4) == "1,2,3,4"


def test_bounded_selection_rejects_a_finite_selection_over_the_limit_without_expanding_it():
    with pytest.raises(PageSelectionLimitError):
        bound_page_selection("1-999999999999999999999999", limit=64)


def test_bounded_selection_accounts_for_finite_pages_before_an_open_range():
    assert bound_page_selection("1,5,10-", limit=4) == "1,5,10,11,12"


@pytest.mark.parametrize("value", ["1\u0662", "1\uff10", "1\U0001d7da"])
def test_page_selection_parser_rejects_unicode_decimal_digits(value: str):
    with pytest.raises(ValueError, match="comma-separated list"):
        parse_page_selection(value)


@given(st.sets(st.integers(min_value=1, max_value=200), min_size=1, max_size=32))
def test_finite_selection_preserves_its_distinct_physical_pages(pages: set[int]):
    selector = ",".join(str(page) for page in sorted(pages, reverse=True))

    bounded = bound_page_selection(selector, limit=len(pages))

    assert _pages(bounded) == pages


@given(
    start=st.integers(min_value=2, max_value=100),
    limit=st.integers(min_value=1, max_value=32),
    data=st.data(),
)
def test_open_selection_has_exactly_one_bounded_sentinel(start: int, limit: int, data: st.DataObject):
    finite = data.draw(
        st.sets(st.integers(min_value=1, max_value=start - 1), max_size=min(limit, start - 1)),
        label="finite pages before the open range",
    )
    selector = ",".join([*(str(page) for page in sorted(finite)), f"{start}-"])

    bounded = _pages(bound_page_selection(selector, limit=limit))

    expected_open = set(range(start, start + (limit - len(finite)) + 1))
    assert bounded == finite | expected_open
    assert len(bounded) == limit + 1
