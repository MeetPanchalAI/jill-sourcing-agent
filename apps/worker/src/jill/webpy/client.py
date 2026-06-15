"""HTTP client for the web-py sourcing API.

The worker is a service account: every request carries ``X-Service-Token`` +
``X-Tenant-Id`` (one tenant per client instance), which is what drives RLS on the
server. Writes that the server upserts (candidate, score, edge) report whether
the row was created vs matched, so the pipeline can count work without
double-counting on re-runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("jill.webpy")


@dataclass
class Upserted:
    data: dict
    created: bool

    @property
    def id(self) -> Any:
        return self.data["id"]


class WebPyError(Exception):
    pass


class WebPyClient:
    def __init__(self, base_url: str, service_token: str, tenant_id: int | str):
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "X-Service-Token": service_token,
                "X-Tenant-Id": str(tenant_id),
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._http.close()

    # --- low level -------------------------------------------------------

    def _post(self, path: str, payload: dict) -> Upserted:
        resp = self._http.post(path, json=payload)
        if resp.status_code not in (200, 201):
            raise WebPyError(f"POST {path} -> {resp.status_code}: {resp.text}")
        return Upserted(data=resp.json(), created=resp.status_code == 201)

    def _get(self, path: str, params: dict | None = None) -> Any:
        resp = self._http.get(path, params=params or {})
        if resp.status_code != 200:
            raise WebPyError(f"GET {path} -> {resp.status_code}: {resp.text}")
        return resp.json()

    # --- sourcing writes -------------------------------------------------

    def upsert_candidate(self, **fields) -> Upserted:
        return self._post("/api/v1/sourcing/candidates/", fields)

    def create_lead_edge(self, **fields) -> Upserted:
        return self._post("/api/v1/sourcing/lead-edges/", fields)

    def upsert_enrichment(self, **fields) -> Upserted:
        return self._post("/api/v1/sourcing/enrichments/", fields)

    def upsert_score(self, **fields) -> Upserted:
        return self._post("/api/v1/sourcing/scores/", fields)

    def create_outreach(self, **fields) -> Upserted:
        return self._post("/api/v1/sourcing/outreach/", fields)

    def create_target(self, **fields) -> Upserted:
        return self._post("/api/v1/sourcing/targets/", fields)

    def finalize_run(self, run_id: int, **fields) -> Upserted:
        return self._post(f"/api/v1/sourcing/runs/{run_id}/finalize/", fields)

    # --- role / target / outreach (CLI surface) --------------------------

    def create_role(self, **fields) -> Upserted:
        return self._post("/api/v1/sourcing/roles/", fields)

    def source_role(self, role_id: int | str) -> Upserted:
        return self._post(f"/api/v1/sourcing/roles/{role_id}/source/", {})

    def add_target(self, **fields) -> Upserted:
        return self.create_target(**fields)

    def approve_outreach(self, draft_id: int | str) -> Upserted:
        return self._post(f"/api/v1/sourcing/outreach/{draft_id}/approve/", {})

    def reject_outreach(self, draft_id: int | str, reason: str = "") -> Upserted:
        return self._post(f"/api/v1/sourcing/outreach/{draft_id}/reject/",
                          {"reason": reason})

    # --- reads -----------------------------------------------------------

    def health(self) -> Any:
        return self._get("/health/")

    def get_role(self, role_id: int | str) -> Any:
        return self._get(f"/api/v1/sourcing/roles/{role_id}/")

    def list_roles(self) -> Any:
        return self._get("/api/v1/sourcing/roles/")

    def list_outreach(self, **params) -> Any:
        return self._get("/api/v1/sourcing/outreach/", params)

    def leads(self, role_id: int | str, **params) -> Any:
        return self._get(f"/api/v1/sourcing/roles/{role_id}/leads/", params)

    def costs(self, role_id: int | str) -> Any:
        return self._get(f"/api/v1/sourcing/roles/{role_id}/costs/")

    def linkedin_status(self) -> Any:
        return self._get("/api/v1/sourcing/linkedin/")

    def linkedin_connect(self, account_name: str, session_cookie: str) -> Upserted:
        return self._post("/api/v1/sourcing/linkedin/connect/",
                          {"account_name": account_name,
                           "session_cookie": session_cookie})
