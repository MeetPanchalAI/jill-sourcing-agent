"""Live Brightdata client — real API, key-gated.

Shape only: maps HTTP status → typed errors and wraps every call in bounded
retry. Not exercised by the test suite (no live key); ``live`` mode engages only
when ``BRIGHTDATA_API_KEY`` is set. The dataset/endpoint ids below are
placeholders to be confirmed against the real Brightdata LinkedIn datasets
(see prd.md §9).
"""

from __future__ import annotations

import logging

import httpx

from ..config import Settings
from .base import BrightdataClient
from .errors import (
    BrightdataAuthError,
    BrightdataNotFound,
    BrightdataRateLimited,
    BrightdataServerError,
)
from .retry import call_with_retry
from .types import EmployeeRef, Experience, Profile

logger = logging.getLogger("jill.brightdata")


class LiveBrightdataClient(BrightdataClient):
    def __init__(self, settings: Settings):
        if not settings.brightdata_api_key:
            raise BrightdataAuthError("BRIGHTDATA_API_KEY required for live mode")
        self._s = settings
        self._http = httpx.Client(
            base_url=settings.brightdata_base_url,
            headers={"Authorization": f"Bearer {settings.brightdata_api_key}"},
            timeout=30.0,
        )

    # --- transport -------------------------------------------------------

    def _get(self, path: str, params: dict) -> dict:
        def _do() -> dict:
            resp = self._http.get(path, params=params)
            if resp.status_code == 429:
                raise BrightdataRateLimited(path)
            if resp.status_code == 404:
                raise BrightdataNotFound(path)
            if resp.status_code >= 500:
                raise BrightdataServerError(f"{resp.status_code} {path}")
            if resp.status_code == 401 or resp.status_code == 403:
                raise BrightdataAuthError(path)
            resp.raise_for_status()
            return resp.json()

        return call_with_retry(
            _do,
            max_attempts=self._s.scrape_max_attempts,
            base_delay=self._s.scrape_base_delay,
            op=f"GET {path}",
        )

    # --- interface -------------------------------------------------------

    def company_employees(self, company: str) -> list[EmployeeRef]:
        data = self._get("/datasets/linkedin/company_employees",
                         {"company": company})
        return [EmployeeRef(**_pick(e, EmployeeRef.__annotations__))
                for e in data.get("employees", [])]

    def profile(self, linkedin_url: str) -> Profile:
        data = self._get("/datasets/linkedin/profile", {"url": linkedin_url})
        exps = [Experience(**_pick(e, Experience.__annotations__))
                for e in data.get("experiences", [])]
        return Profile(experiences=exps,
                       **_pick(data, Profile.__annotations__, drop={"experiences"}))

    def network(self, profile: Profile, limit: int = 10) -> list[EmployeeRef]:
        data = self._get("/datasets/linkedin/connections",
                         {"url": profile.linkedin_url, "limit": limit})
        return [EmployeeRef(**_pick(e, EmployeeRef.__annotations__))
                for e in data.get("connections", [])][:limit]


def _pick(d: dict, allowed: dict, drop: set | None = None) -> dict:
    """Keep only keys the dataclass accepts (real APIs return extra fields)."""
    drop = drop or set()
    return {k: v for k, v in d.items() if k in allowed and k not in drop}
