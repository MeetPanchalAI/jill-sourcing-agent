"""Prompt builders for Jill's Claude calls."""

from __future__ import annotations

import json

from ..brightdata.types import Profile

PLAN_SYSTEM = (
    "You are Jill, an expert technical recruiter planning where to source for a "
    "role. Given the role title and ICP, name the companies whose employees are "
    "most likely to be strong fits — known for the role's domain, stack, and "
    "stage. Prefer focused, high-signal companies over giant generalists. For "
    "EACH, give the company name, its LinkedIn company URL "
    "(https://www.linkedin.com/company/<slug> — your best-known slug for that "
    "exact company), and a one-line reason tied to the ICP. Return the strongest "
    "first. Name only real companies you are confident exist; never invent a slug "
    "you are unsure of — leave linkedin_url empty rather than guess wrongly."
)


def build_plan_user(role_title: str, icp: dict, n: int, exclude: list[str] | None = None) -> str:
    exclude_block = (
        "\n\nALREADY TRIED (propose DIFFERENT companies, do not repeat these):\n"
        + json.dumps(exclude, indent=2)
        if exclude else ""
    )
    return (
        f"ROLE: {role_title}\n\nICP:\n"
        + json.dumps(icp, indent=2)
        + exclude_block
        + f"\n\nPropose the top {n} companies to source candidates from. Return a "
        "list of {{name, linkedin_url, reason}}, strongest first."
    )


SCORE_SYSTEM = (
    "You are Jill, an expert technical recruiter scoring how well a candidate "
    "fits a role. You are given a weighted RUBRIC of criteria (e.g. ex-founder, "
    "school pedigree, a language, domain experience, tenure band, and open-ended "
    "signals). For EACH rubric criterion, judge status as 'met', 'partial', or "
    "'missed' with a short grounded `detail` citing the candidate's profile. "
    "Then give an overall 0-100 `score` (weighted by the criteria), a one-line "
    "`summary` a recruiter can skim, and a verdict ('fit' or 'drop'). Ground "
    "everything in the profile — never invent schools, employers, or skills. "
    "LinkedIn data is uneven: a profile may have an empty `skills` list or sparse "
    "`experiences` yet still describe its stack and domain in `about`/`headline` — "
    "infer skill and domain criteria from that free text too, not just the "
    "structured fields. A 'drop' must state what is missing."
)


def _profile_view(profile: Profile) -> dict:
    return {
        "headline": profile.headline,
        "about": profile.about,
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
