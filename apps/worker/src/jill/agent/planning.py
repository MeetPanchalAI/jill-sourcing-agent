"""Seed planning (P1) — derive the companies to source from out of a role's ICP.

``Planner`` is the interface; ``StubPlanner`` is the deterministic mock (offline,
no LLM), and ``get_planner`` returns the Claude-backed planner in live mode. The
pipeline calls this only when a role has no recruiter-provided seeds, so the
human-entered seeds always win.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..config import Settings, get_settings
from .schemas import SeedCompany, SeedPlan


@runtime_checkable
class Planner(Protocol):
    model_id: str

    def propose_seeds(self, role_title: str, icp: dict, n: int = 3,
                      exclude: list[str] | None = None) -> SeedPlan: ...


# A small fixed roster the stub draws from (deterministic, offline). Ordered so
# replanning (which excludes earlier picks) keeps yielding fresh companies.
_STUB_ROSTER = [
    ("Vapi", "https://www.linkedin.com/company/vapi-ai"),
    ("Retell AI", "https://www.linkedin.com/company/retellai"),
    ("Deepgram", "https://www.linkedin.com/company/deepgram"),
    ("ElevenLabs", "https://www.linkedin.com/company/elevenlabs"),
    ("AssemblyAI", "https://www.linkedin.com/company/assemblyai"),
]


class StubPlanner:
    """Deterministic mock planner — echoes any companies already in the ICP, else
    draws from a fixed roster, so a mock run is reproducible without a network
    call. Honors ``exclude`` so replanning yields fresh companies."""

    model_id = "mock:rules"

    def propose_seeds(self, role_title: str, icp: dict, n: int = 3,
                      exclude: list[str] | None = None) -> SeedPlan:
        skip = {e.strip().lower() for e in (exclude or [])}
        existing = [
            c for c in (icp.get("target_companies") or [])
            if (c.get("name") if isinstance(c, dict) else c)
        ]
        picks: list[SeedCompany] = []
        if existing and not exclude:
            picks = [
                SeedCompany(
                    name=(c.get("name") if isinstance(c, dict) else c),
                    linkedin_url=(c.get("linkedin_url", "") if isinstance(c, dict) else ""),
                    reason="recruiter-provided seed",
                )
                for c in existing
            ]
        for name, url in _STUB_ROSTER:
            if len(picks) >= n:
                break
            if name.lower() in skip or url.lower() in skip:
                continue
            picks.append(SeedCompany(name=name, linkedin_url=url,
                                     reason="voice-AI / realtime-audio peer"))
        return SeedPlan(companies=picks[:n] or [
            SeedCompany(name=name, linkedin_url=url, reason="fallback")
            for name, url in _STUB_ROSTER[:1]
        ])


def get_planner(settings: Settings | None = None) -> Planner:
    settings = settings or get_settings()
    if settings.is_live:
        from .claude import ClaudePlanner  # lazy: avoid anthropic import in mock paths

        return ClaudePlanner(settings)
    return StubPlanner()
