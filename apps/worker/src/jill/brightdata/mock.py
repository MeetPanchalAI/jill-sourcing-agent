"""Fixture-backed Brightdata client — deterministic, zero network (C2).

Loads ``fixtures/companies/<slug>.json`` and ``fixtures/profiles/<slug>.json``.
Logs only identifiers and counts, never raw profile text (C4 / T6.3).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .base import BrightdataClient
from .errors import BrightdataNotFound
from .types import EmployeeRef, Experience, Profile

logger = logging.getLogger("jill.brightdata")

_FIXTURES = Path(__file__).parent / "fixtures"


def _slug(value: str) -> str:
    """Normalize a company name or LinkedIn URL to a fixture slug.

    "Vapi" → "vapi", "Retell AI" → "retell-ai",
    "https://linkedin.com/company/vapi" → "vapi",
    "https://linkedin.com/in/alice-nguyen" → "alice-nguyen".
    """
    value = value.strip().rstrip("/")
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    value = value.lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def _load(path: Path) -> dict:
    if not path.exists():
        raise BrightdataNotFound(f"no fixture at {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


class MockBrightdataClient(BrightdataClient):
    network_method = "shared_company"  # fixtures model a shared-company cohort

    def company_employees(self, company: str) -> list[EmployeeRef]:
        slug = _slug(company)
        data = _load(_FIXTURES / "companies" / f"{slug}.json")
        employees = [EmployeeRef(**e) for e in data.get("employees", [])]
        logger.debug("brightdata.company_employees %s -> %d employees",
                    slug, len(employees))
        return employees

    def profile(self, linkedin_url: str) -> Profile:
        slug = _slug(linkedin_url)
        data = _load(_FIXTURES / "profiles" / f"{slug}.json")
        exps = [Experience(**e) for e in data.pop("experiences", [])]
        prof = Profile(experiences=exps, **data)
        logger.debug("brightdata.profile %s -> %d experiences, %d skills",
                    slug, len(prof.experiences), len(prof.skills))
        return prof

    def network(self, profile: Profile, limit: int = 10) -> list[EmployeeRef]:
        """Approximate the network via shared-company cohorts (see plan.md)."""
        seen: set[str] = {profile.linkedin_url}
        cohort: list[EmployeeRef] = []
        for company in profile.previous_companies():
            try:
                peers = self.company_employees(company)
            except BrightdataNotFound:
                continue
            for ref in peers:
                if ref.linkedin_url in seen:
                    continue
                seen.add(ref.linkedin_url)
                cohort.append(ref)
                if len(cohort) >= limit:
                    break
            if len(cohort) >= limit:
                break
        logger.debug("brightdata.network %s -> %d peers (shared_company)",
                    _slug(profile.linkedin_url), len(cohort))
        return cohort
