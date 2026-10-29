from datetime import date

import pytest

from chatcore.errors import InvalidRequest
from chatcore.filters import And, Eq, In, Not, Or, Range, all_of, compile_predicate, in_

ALLOWED = {"project_id", "date", "tags", "confidential"}


def test_empty_in_allows_nothing() -> None:
    pred = compile_predicate(in_("project_id", []), ALLOWED)
    assert not pred({"project_id": "P-1"})


def test_list_valued_field_matches_any() -> None:
    pred = compile_predicate(Eq("tags", "risk"), ALLOWED)
    assert pred({"tags": ["schedule", "risk"]})
    assert not pred({"tags": ["schedule"]})


def test_date_range_coerces_iso_strings() -> None:
    pred = compile_predicate(Range("date", gte=date(2026, 3, 1), lte=date(2026, 3, 31)), ALLOWED)
    assert pred({"date": "2026-03-15T09:00:00"})
    assert not pred({"date": "2026-04-01"})
    assert not pred({"date": None})


def test_boolean_composition() -> None:
    f = And((In("project_id", ("P-1", "P-2")), Or((Eq("confidential", False), Not(Eq("tags", "x"))))))
    pred = compile_predicate(f, ALLOWED)
    assert pred({"project_id": "P-1", "confidential": False, "tags": ["x"]})
    assert not pred({"project_id": "P-3", "confidential": False, "tags": []})


def test_unknown_field_rejected_at_compile_time() -> None:
    with pytest.raises(InvalidRequest):
        compile_predicate(Eq("owner_email", "a"), ALLOWED)


def test_value_cannot_change_filter_structure() -> None:
    # 값은 값일 뿐이다. 필터 문법처럼 생긴 문자열도 그대로 비교된다.
    pred = compile_predicate(Eq("project_id", "P-1' or 1 eq 1"), ALLOWED)
    assert not pred({"project_id": "P-1"})


def test_all_of_skips_missing_parts() -> None:
    assert all_of(None, None) is None
    only = Eq("project_id", "P-1")
    assert all_of(None, only) is only
