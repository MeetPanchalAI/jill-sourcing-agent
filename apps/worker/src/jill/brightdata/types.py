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
        """Companies other than the current one — the prev-employer fan-out seeds."""
        out: list[str] = []
        for exp in self.experiences:
            if exp.end is not None and exp.company and exp.company not in out:
                out.append(exp.company)
        return out
