"""Serializable types crossing the workflow/activity boundary.

Plain dataclasses (Temporal's default converter handles them). Kept import-light
so the workflow sandbox can load this module without pulling in httpx/anthropic.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourcingInput:
    role_id: int
    run_id: int
    tenant_id: int
    role_title: str
    icp: dict
    seed_companies: list[str]
    max_depth: int = 2
    window_days: int = 90
    max_leads: int = 50
    max_companies: int = 50


@dataclass
class ScanArgs:
    role_id: int
    run_id: int
    tenant_id: int
    company: str
    depth: int
    as_of: str  # ISO date, supplied by the workflow's deterministic clock
    window_days: int


@dataclass
class ScanResult:
    new_candidates: int
    leads: list[dict] = field(default_factory=list)  # [{id, linkedin_url}]


@dataclass
class EvalArgs:
    role_id: int
    run_id: int
    tenant_id: int
    candidate_id: int
    linkedin_url: str
    role_title: str
    icp: dict
    depth: int
    max_depth: int


@dataclass
class EvalResult:
    is_fit: bool
    drafted: int
    skipped: bool
    prev_employer_companies: list[str] = field(default_factory=list)
    network_leads: list[dict] = field(default_factory=list)


@dataclass
class FinalizeArgs:
    run_id: int
    tenant_id: int
    status: str
    counters: dict = field(default_factory=dict)
