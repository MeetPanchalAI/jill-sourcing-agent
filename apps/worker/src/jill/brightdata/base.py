"""The Brightdata client interface — the single seam through which all LinkedIn
data enters the system (C1). Concrete impls: ``MockBrightdataClient`` (fixtures)
and ``LiveBrightdataClient`` (real API, key-gated)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import EmployeeRef, Profile


class BrightdataClient(ABC):
    @abstractmethod
    def company_employees(self, company: str) -> list[EmployeeRef]:
        """Shallow listing of a company's current employees (for joiner detection).

        ``company`` is a name or LinkedIn company URL."""

    @abstractmethod
    def profile(self, linkedin_url: str) -> Profile:
        """Deep profile for one person (enrichment)."""

    @abstractmethod
    def network(self, profile: Profile, limit: int = 10) -> list[EmployeeRef]:
        """Approximate a person's reachable network.

        Brightdata rarely exposes raw connections, so the network is
        approximated from shared-company cohorts; callers record the method on
        the ``LeadEdge`` (see plan.md decision log)."""
