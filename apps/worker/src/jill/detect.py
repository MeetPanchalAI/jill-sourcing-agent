"""Recent-joiner detection — pure, deterministic (no I/O, no clock).

``as_of`` is passed in rather than read from the system clock so the function is
safe to call from deterministic Temporal workflow code and trivial to test at
window boundaries. Employees with an unparseable/missing ``started_at`` are
excluded (and counted), never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .brightdata.types import EmployeeRef


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


@dataclass
class DetectionResult:
    recent: list[EmployeeRef] = field(default_factory=list)
    total: int = 0
    excluded_old: int = 0
    excluded_no_date: int = 0
    deduped: int = 0

    @property
    def recent_count(self) -> int:
        return len(self.recent)


def detect_recent_joiners(
    employees: list[EmployeeRef],
    as_of: date,
    window_days: int,
) -> DetectionResult:
    """Keep employees who started their current role within ``window_days`` of
    ``as_of``. Dedupes by LinkedIn URL (first occurrence wins)."""
    result = DetectionResult(total=len(employees))
    seen: set[str] = set()
    for emp in employees:
        if emp.linkedin_url in seen:
            result.deduped += 1
            continue
        seen.add(emp.linkedin_url)

        started = _parse_date(emp.started_at)
        if started is None:
            result.excluded_no_date += 1
            continue
        age_days = (as_of - started).days
        if age_days > window_days:
            result.excluded_old += 1
            continue
        result.recent.append(emp)
    return result
