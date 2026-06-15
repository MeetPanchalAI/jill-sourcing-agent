"""Outreach drafting — the second judgment point.

``Drafter`` is the interface; ``TemplateDrafter`` is the deterministic mock (test
default) and ``get_drafter`` returns the Claude drafter in live mode. Both
personalize from a ``DraftContext`` built from real profile + provenance facts —
no fabricated mutual connections or details (C19).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..brightdata.types import Profile
from ..config import Settings, get_settings
from .schemas import OutreachResult

_HOOKS = {
    "recent_joiner": "your recent move to {company}",
    "network": "your shared background with our network around {origin}",
    "prev_employer": "your time at {origin}",
}


@dataclass
class DraftContext:
    candidate_name: str
    candidate_title: str
    candidate_company: str
    role_title: str
    hook: str
    matched_reasons: list[str] = field(default_factory=list)

    @property
    def first_name(self) -> str:
        return self.candidate_name.split()[0] if self.candidate_name else "there"


def build_context(
    profile: Profile,
    *,
    role_title: str,
    reasons: list[str],
    source_kind: str = "recent_joiner",
    origin: str = "",
) -> DraftContext:
    template = _HOOKS.get(source_kind, "your work at {company}")
    hook = template.format(
        company=profile.current_company, origin=origin or profile.current_company
    )
    return DraftContext(
        candidate_name=profile.full_name,
        candidate_title=profile.current_title,
        candidate_company=profile.current_company,
        role_title=role_title,
        hook=hook,
        matched_reasons=reasons,
    )


@runtime_checkable
class Drafter(Protocol):
    model_id: str

    def draft(self, ctx: DraftContext, channel: str) -> OutreachResult: ...


class TemplateDrafter:
    """Deterministic, grounded template fill. Interpolates only ``ctx`` fields."""

    model_id = "mock:template"

    def draft(self, ctx: DraftContext, channel: str) -> OutreachResult:
        if channel == "email":
            subject = f"{ctx.role_title} — your background at {ctx.candidate_company}"
            body = (
                f"Hi {ctx.first_name},\n\n"
                f"I came across your profile and noticed {ctx.hook}. We're hiring a "
                f"{ctx.role_title} and your experience as {ctx.candidate_title} at "
                f"{ctx.candidate_company} stood out"
            )
            if ctx.matched_reasons:
                body += f" — {ctx.matched_reasons[0].lower()}"
            body += ".\n\nWould you be open to a short conversation?\n\n— Jill"
            return OutreachResult(subject=subject, body=body)

        # linkedin: short, no subject
        body = (
            f"Hi {ctx.first_name}, I noticed {ctx.hook}. We're hiring a "
            f"{ctx.role_title} and your background at {ctx.candidate_company} caught "
            "my eye — open to a quick chat?"
        )
        return OutreachResult(subject="", body=body)


def get_drafter(settings: Settings | None = None) -> Drafter:
    settings = settings or get_settings()
    if settings.is_live:
        from .claude import ClaudeDrafter  # lazy

        return ClaudeDrafter(settings)
    return TemplateDrafter()
