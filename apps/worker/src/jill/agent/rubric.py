"""Structured rubric scoring — the crisp, per-criterion fit assessment.

A role's ICP can carry a ``rubric``: a weighted list of criteria, each of a known
*type* the evaluator knows how to judge from a candidate's profile:

    {"name": "Ex-founder",   "type": "founder",  "weight": 2}
    {"name": "Pedigree",     "type": "pedigree", "schools": [...], "weight": 2}
    {"name": "Python",       "type": "skill",    "skill": "Python", "weight": 1}
    {"name": "Voice domain", "type": "domain",   "keywords": [...], "weight": 2}
    {"name": "Tenure 2-6y",  "type": "tenure",   "min_years": 2, "max_years": 6}
    {"name": "0-to-1",       "type": "open",     "description": "early-stage"}

Every criterion returns ``met | partial | missed`` with grounded ``detail``. The
overall 0-100 score is the weighted fraction; a crisp ``summary`` is composed from
the met criteria. ``open`` criteria are deterministically neutral (``partial``)
here — the live Claude scorer judges them properly.
"""

from __future__ import annotations

from datetime import date

from ..brightdata.types import Profile
from .schemas import CriterionResult

# Elite institutions used when a pedigree criterion doesn't list its own schools.
ELITE_SCHOOLS = [
    "IIT", "NIT", "BITS", "IISc", "IIIT", "Stanford", "MIT", "CMU", "Carnegie",
    "Berkeley", "Harvard", "Princeton", "Caltech", "Oxford", "Cambridge",
    "Georgia Tech", "Waterloo", "ETH",
]

_FOUNDER_KW = ("founder", "co-founder", "cofounder", "founding")
_STATUS_VALUE = {"met": 1.0, "partial": 0.5, "missed": 0.0}


def _texts(profile: Profile) -> dict:
    titles = [profile.current_title, *(e.title for e in profile.experiences)]
    companies = [profile.current_company, *(e.company for e in profile.experiences)]
    schools = [str(e.get("school", "")) for e in profile.education]
    full = " ".join([*profile.skills, *titles, *companies,
                     profile.headline, profile.about]).lower()
    return {
        "titles": " ".join(t for t in titles if t).lower(),
        "schools": [s for s in schools if s],
        "skills": [s.lower() for s in profile.skills],
        "full": full,
    }


def _career_years(profile: Profile, as_of: date) -> float:
    starts = []
    for exp in profile.experiences:
        if exp.start:
            try:
                starts.append(date.fromisoformat(exp.start[:10]))
            except (ValueError, TypeError):
                pass
    if not starts:
        return 0.0
    return round((as_of - min(starts)).days / 365.25, 1)


def _eval_one(crit: dict, t: dict, profile: Profile, as_of: date) -> CriterionResult:
    ctype = crit.get("type", "open")
    weight = float(crit.get("weight", 1))
    name = crit.get("name", ctype)

    if ctype == "founder":
        hit = next((kw for kw in _FOUNDER_KW if kw in t["titles"]), None)
        status = "met" if hit else "missed"
        detail = "founder/founding role in history" if hit else "no founder role"

    elif ctype == "pedigree":
        schools = crit.get("schools") or ELITE_SCHOOLS
        match = next(
            (sc for sc in t["schools"]
             for elite in schools if elite.lower() in sc.lower()), None
        )
        status = "met" if match else "missed"
        detail = match or "no elite-school match"

    elif ctype == "skill":
        wanted = [crit["skill"]] if crit.get("skill") else crit.get("skills", [])
        matched = [w for w in wanted if w.lower() in t["full"]]
        status = "met" if matched else "missed"
        detail = ", ".join(matched) if matched else f"missing {', '.join(wanted)}"

    elif ctype == "domain":
        kws = crit.get("keywords", [])
        matched = [k for k in kws if k.lower() in t["full"]]
        status = "met" if matched else "missed"
        detail = ", ".join(matched) if matched else "no domain signal"

    elif ctype == "tenure":
        years = _career_years(profile, as_of)
        lo, hi = crit.get("min_years", 0), crit.get("max_years", 99)
        if lo <= years <= hi:
            status = "met"
        elif lo - 1 <= years <= hi + 1:
            status = "partial"
        else:
            status = "missed"
        detail = f"{years:.1f}y experience (want {lo}-{hi}y)"

    else:  # open-ended — deterministic evaluator can't judge; stay neutral
        status = "partial"
        detail = crit.get("description", "open-ended — needs review")

    return CriterionResult(name=name, weight=weight, status=status, detail=detail)


def build_summary(criteria: list[CriterionResult]) -> str:
    met = [c.name for c in criteria if c.status == "met"]
    n_met = sum(1 for c in criteria if c.status == "met")
    head = ", ".join(met) if met else "no criteria met"
    return f"{head} - {n_met}/{len(criteria)} criteria met"


def evaluate_rubric(
    rubric: list[dict], profile: Profile, as_of: date | None = None
) -> tuple[list[CriterionResult], int, str]:
    """Return (per-criterion results, 0-100 weighted score, summary)."""
    as_of = as_of or date.today()
    t = _texts(profile)
    results = [_eval_one(c, t, profile, as_of) for c in rubric]
    total_w = sum(c.weight for c in results) or 1.0
    score = round(100 * sum(c.weight * _STATUS_VALUE[c.status]
                            for c in results) / total_w)
    return results, score, build_summary(results)
