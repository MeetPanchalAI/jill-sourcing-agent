"""Wire types returned by the Brightdata client.

Plain dataclasses so they serialize cleanly across Temporal activity
boundaries (the default JSON converter handles dataclasses with type hints).
``started_at`` is an ISO date string (or None) — parsing/﻿windowing is the
detection layer's job, kept out of the I/O layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EmployeeRef:
    """A person as seen in a company-employee listing (shallow)."""

    linkedin_url: str
    full_name: str = ""
    headline: str = ""
    current_title: str = ""
    current_company: str = ""
    started_at: str | None = None  # ISO date the person started this role
    location: str = ""


@dataclass
class Experience:
    company: str
    title: str
    start: str | None = None
    end: str | None = None  # None ⇒ current role
    company_url: str = ""    # LinkedIn company URL, when the source provides it
    # What they actually did in the role (LinkedIn ``description``, HTML stripped).
    # The dataset has no ``skills`` field, so this free text is the signal the
    # scorer + triage mine for skills/domain.
    description: str = ""


@dataclass
class Profile:
    """A fully enriched profile (deep)."""

    linkedin_url: str
    full_name: str = ""
    headline: str = ""
    about: str = ""  # the profile's free-text summary — richest fit signal
    location: str = ""
    current_company: str = ""
    # The current company's LinkedIn URL — the canonical key used to promote a
    # surviving candidate's company into the next workflow's seed set.
    current_company_url: str = ""
    current_title: str = ""
    started_at: str | None = None
    experiences: list[Experience] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    education: list[dict] = field(default_factory=list)
    # LinkedIn's "people also viewed" — the network-expansion edge. Each entry:
    # {profile_link, name, about, location}. Often the only growth signal we get.
    people_also_viewed: list[dict] = field(default_factory=list)

    def previous_companies(self) -> list[str]:
        """Companies the person no longer works at — the prev-employer fan-out seeds.
        A company is *current* if any role there is ongoing (``end is None``); those
        are excluded. Prefer each company's URL (unambiguous to scan) over its name."""
        current: set[str] = set()
        order: list[str] = []
        seed_of: dict[str, str] = {}
        for exp in self.experiences:
            if not exp.company:
                continue
            key = (exp.company_url or exp.company).strip().lower()
            if not key:
                continue
            if key not in seed_of:
                seed_of[key] = exp.company_url or exp.company
                order.append(key)
            if exp.end is None:
                current.add(key)
        return [seed_of[k] for k in order if k not in current]
