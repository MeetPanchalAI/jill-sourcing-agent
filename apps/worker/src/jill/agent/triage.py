"""Surface triage (P-surface) — the *recall* gate, distinct from fit scoring.

The fit rubric (``scoring.py``) is a *precision* judgement: it demands evidence
(Python, founder, pedigree, 0-to-1) and rightly drops a candidate when that
evidence is absent. But LinkedIn data is often shallow (public-preview profiles
with no experience/skills), so using the rubric to decide *who makes the list*
throws away relevant people we simply couldn't verify.

Triage answers a different, permissive question: **is this person worth putting
on the shortlist and expanding from?** It uses only signals we reliably have —
the current company's domain and obvious off-role markers — and defaults to
KEEP when the profile is too sparse to judge. The recruiter rejects from the
list; we don't pre-reject by absence of evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..brightdata.types import Profile

# Roles that are clearly not the engineering/builder target — reject from the
# shortlist when the profile *affirmatively* shows one of these.
_OFF_ROLE = (
    "recruiter", "talent acquisition", "sourcer", "marketing", "brand ",
    "sales", "account executive", "counsel", "attorney", "legal ",
    "people operations", "human resources", " hr ", "investor", "venture",
    "trainer", "compliance", "customer success", "business development",
    "accountant", "controller", "office manager", "executive assistant",
)


@dataclass
class TriageResult:
    keep: bool
    reason: str


def _domain_keywords(icp: dict) -> list[str]:
    """Reliable role/domain signal terms drawn from the ICP."""
    kws: list[str] = []
    for crit in icp.get("rubric", []) or []:
        if crit.get("type") == "domain":
            kws += [k.lower() for k in crit.get("keywords", [])]
        if crit.get("type") == "skill" and crit.get("skill"):
            kws.append(str(crit["skill"]).lower())
    kws += [s.lower() for s in icp.get("must_have_skills", []) or []]
    return [k for k in dict.fromkeys(kws) if k]  # de-dupe, keep order


def surface_triage(profile: Profile, icp: dict) -> TriageResult:
    """Permissive keep/reject for the shortlist + expansion.

    KEEP when the profile is domain-relevant or too sparse to judge; REJECT only
    when it affirmatively shows an off-role (recruiter, marketing, investor, …)."""
    text = " ".join(filter(None, [
        profile.headline, profile.about, profile.current_title,
        profile.current_company, *[e.title for e in profile.experiences],
        *[e.company for e in profile.experiences],
    ])).lower()

    off = next((m for m in _OFF_ROLE if m.strip() in text), None)
    if off:
        return TriageResult(False, f"off-role signal: {off.strip()!r}")

    domain = _domain_keywords(icp)
    hit = next((k for k in domain if k in text), None)
    if hit:
        return TriageResult(True, f"domain-relevant: {hit!r}")

    # Too little signal to judge — keep for manual review (recall over precision).
    return TriageResult(True, "kept for review (sparse profile)")
