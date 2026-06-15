"""P3 gate (tests.md §2): recent-joiner detection — window boundary, missing
dates, dedup. Pure function, deterministic via an injected ``as_of``."""

from __future__ import annotations

from datetime import date

from jill.brightdata.types import EmployeeRef
from jill.detect import detect_recent_joiners

AS_OF = date(2026, 6, 14)
WINDOW = 90


def _emp(slug: str, started: str | None) -> EmployeeRef:
    return EmployeeRef(linkedin_url=f"https://linkedin.com/in/{slug}",
                       full_name=slug, started_at=started)


def test_window_boundary_inclusive():
    # 89 and 90 days old are recent; 91 is too old.
    employees = [
        _emp("d89", "2026-03-17"),   # 89 days before AS_OF
        _emp("d90", "2026-03-16"),   # exactly 90
        _emp("d91", "2026-03-15"),   # 91 -> excluded
    ]
    res = detect_recent_joiners(employees, AS_OF, WINDOW)
    slugs = {e.full_name for e in res.recent}
    assert slugs == {"d89", "d90"}
    assert res.excluded_old == 1


def test_missing_or_bad_date_excluded():
    employees = [
        _emp("nodate", None),
        _emp("baddate", "not-a-date"),
        _emp("good", "2026-06-01"),
    ]
    res = detect_recent_joiners(employees, AS_OF, WINDOW)
    assert [e.full_name for e in res.recent] == ["good"]
    assert res.excluded_no_date == 2


def test_dedup_by_url():
    e = _emp("alice", "2026-06-01")
    res = detect_recent_joiners([e, e, e], AS_OF, WINDOW)
    assert res.recent_count == 1
    assert res.deduped == 2


def test_future_start_is_recent():
    res = detect_recent_joiners([_emp("future", "2026-07-01")], AS_OF, WINDOW)
    assert res.recent_count == 1


def test_counts_add_up():
    employees = [
        _emp("a", "2026-06-01"),     # recent
        _emp("b", "2020-01-01"),     # old
        _emp("c", None),             # no date
        _emp("a", "2026-06-01"),     # dup
    ]
    res = detect_recent_joiners(employees, AS_OF, WINDOW)
    assert res.total == 4
    assert res.recent_count == 1
    assert res.excluded_old == 1
    assert res.excluded_no_date == 1
    assert res.deduped == 1
