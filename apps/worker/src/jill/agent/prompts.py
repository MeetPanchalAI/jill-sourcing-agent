"""Prompt builders for Jill's Claude calls."""

from __future__ import annotations

import json

from ..brightdata.types import Profile

SCORE_SYSTEM = (
    "You are Jill, an expert technical recruiter scoring how well a candidate "
    "fits a role. You are given a weighted RUBRIC of criteria (e.g. ex-founder, "
    "school pedigree, a language, domain experience, tenure band, and open-ended "
    "signals). For EACH rubric criterion, judge status as 'met', 'partial', or "
    "'missed' with a short grounded `detail` citing the candidate's profile. "
    "Then give an overall 0-100 `score` (weighted by the criteria), a one-line "
    "`summary` a recruiter can skim, and a verdict ('fit' or 'drop'). Ground "
    "everything in the profile — never invent schools, employers, or skills. A "
    "'drop' must state what is missing."
)


def _profile_view(profile: Profile) -> dict:
    return {
        "headline": profile.headline,
        "current_title": profile.current_title,
        "current_company": profile.current_company,
        "location": profile.location,
        "skills": profile.skills,
        "education": profile.education,
        "experiences": [
            {"company": e.company, "title": e.title, "start": e.start, "end": e.end}
            for e in profile.experiences
        ],
    }


DRAFT_SYSTEM = (
    "You are Jill, a recruiter writing a brief, warm outreach message inviting a "
    "candidate to a role. Personalize using ONLY the facts provided — the "
    "candidate's name, title, company, why they fit, and how we found them. Never "
    "invent mutual connections, shared schools, or any detail not given. Keep it "
    "concise, specific, and human. For LinkedIn leave the subject empty."
)


def build_draft_user(ctx: dict, channel: str) -> str:
    return (
        f"CHANNEL: {channel}\n\nCONTEXT (use only these facts):\n"
        + json.dumps(ctx, indent=2)
        + "\n\nWrite the invite. Return a subject (empty for LinkedIn) and body."
    )


def build_score_user(icp: dict, profile: Profile) -> str:
    rubric = icp.get("rubric")
    rubric_block = (
        "RUBRIC (score each criterion):\n" + json.dumps(rubric, indent=2)
        if rubric
        else "ROLE ICP:\n" + json.dumps(icp, indent=2)
    )
    return (
        rubric_block
        + "\n\nCANDIDATE PROFILE:\n"
        + json.dumps(_profile_view(profile), indent=2)
        + "\n\nReturn: per-criterion results (name, weight, status, detail), an "
        "overall 0-100 score, a one-line summary, the verdict, and a drop_reason "
        "if dropping."
    )
