"""Deep profile enrichment via Apify.

Brightdata surfaces a company's employees (name + title + recent school) but not
the deep profile the rubric scores on — full work history, skills, education. So
once we have an employee's LinkedIn URL, we enrich it through an Apify LinkedIn
profile actor (default ``harvestapi/linkedin-profile-scraper``) and map the rich
record onto our ``Profile`` type. Brightdata stays the *surfacing* layer; Apify
is the *detail* layer. Key-gated like live Brightdata.

The actor is run synchronously (``run-sync-get-dataset-items``): one POST returns
the parsed dataset items. Field *names* and counts are logged, never the profile
text (C4).
"""

from __future__ import annotations

import logging

import httpx

from .brightdata.errors import BrightdataNotFound, BrightdataRateLimited
from .brightdata.types import Experience, Profile
from .config import Settings

logger = logging.getLogger("jill.apify")


class ApifyError(RuntimeError):
    """Apify call failed in a way that should surface (bad token, un-approved
    actor, server error) rather than silently skip the candidate."""


class ApifyEnricher:
    def __init__(self, settings: Settings):
        if not settings.apify_api_key:
            raise ApifyError("APIFY_API_KEY required for Apify enrichment")
        self._s = settings
        self._http = httpx.Client(base_url=settings.apify_base_url, timeout=180.0)

    def profile(self, linkedin_url: str) -> Profile:
        """Enrich one LinkedIn profile URL → a deep ``Profile``."""
        actor = self._s.apify_profile_actor
        path = f"/v2/acts/{actor}/run-sync-get-dataset-items"
        logger.debug("apify.run actor=%s url=%s", actor, linkedin_url)
        resp = self._http.post(
            path, params={"token": self._s.apify_api_key},
            json={"urls": [linkedin_url], "profileScraperMode": self._s.apify_profile_mode},
        )
        if resp.status_code in (401, 403):
            # 403 here is usually "actor-not-approved" — a one-time console click.
            raise ApifyError(
                f"apify {resp.status_code} for actor {actor!r} — likely needs a "
                f"one-time permission approval in the Apify console: {resp.text[:300]}"
            )
        if resp.status_code == 404:
            raise BrightdataNotFound(linkedin_url)
        if resp.status_code == 429:
            raise BrightdataRateLimited("apify")
        if resp.status_code >= 400:
            raise ApifyError(f"apify {resp.status_code}: {resp.text[:300]}")

        items = resp.json()
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            logger.warning("apify.profile %s -> EMPTY result (items=%s)",
                           linkedin_url, type(items).__name__)
            raise BrightdataNotFound(linkedin_url)
        item = items[0]
        # The actor returns HTTP 201 even on account-level failures (e.g. the free
        # 10-run cap) — the failure rides in an ``error`` field on the item. Surface
        # it loudly so the run doesn't silently DROP everyone on an empty profile.
        if item.get("error") and not (item.get("firstName") or item.get("experience")):
            raise ApifyError(f"apify actor error for {linkedin_url}: {item['error']}")
        # Visibility into what Apify actually returned — field *names* + counts so
        # we can see whether the deep fields are present (C4: keys/counts, not text).
        logger.info(
            "apify.profile %s -> keys=%s | experience=%d skills=%d education=%d "
            "about=%dch company=%r",
            linkedin_url, sorted(item.keys()),
            len(item.get("experience") or []),
            len(item.get("skills") or []),
            len(item.get("education") or []),
            len(item.get("about") or ""),
            _current(item).get("companyName") or "-",
        )
        return _apify_to_profile(item, linkedin_url)


# --- field mapping (harvestapi/linkedin-profile-scraper record → our Profile) ---
# Field names confirmed against a live run; mapped defensively for minor drift.

def _clean_url(u) -> str:
    return u.split("?")[0].rstrip("/").strip() if isinstance(u, str) else ""


def _date_text(v) -> str:
    """A date is {month, year, text} | "Present" | str. Return a readable string."""
    if isinstance(v, dict):
        return v.get("text") or (str(v.get("year")) if v.get("year") else "")
    return v or ""


def _current(item: dict) -> dict:
    """The current role (first ``currentPosition``, else first ``experience``)."""
    cur = item.get("currentPosition") or []
    if cur and isinstance(cur[0], dict):
        return cur[0]
    exp = item.get("experience") or []
    return exp[0] if exp and isinstance(exp[0], dict) else {}


def _apify_to_profile(item: dict, fallback_url: str) -> Profile:
    full_name = (
        " ".join(filter(None, [item.get("firstName"), item.get("lastName")])).strip()
        or item.get("name") or ""
    )
    loc = item.get("location") or {}
    location = ""
    if isinstance(loc, dict):
        location = loc.get("linkedinText") or (loc.get("parsed") or {}).get("text") or ""
    elif isinstance(loc, str):
        location = loc

    experiences: list[Experience] = []
    for e in (item.get("experience") or []):
        if not isinstance(e, dict):
            continue
        end = _date_text(e.get("endDate"))
        if end.strip().lower() in ("present", "current", ""):
            end = None
        experiences.append(Experience(
            company=e.get("companyName") or "",
            company_url=_clean_url(e.get("companyLinkedinUrl")),
            title=e.get("position") or e.get("title") or "",
            start=_date_text(e.get("startDate")) or None,
            end=end,
        ))

    skills = [
        (s.get("name") if isinstance(s, dict) else s)
        for s in (item.get("skills") or [])
    ]
    education = [
        {"school": e.get("schoolName") or e.get("school"),
         "degree": e.get("degree"), "field": e.get("fieldOfStudy")}
        for e in (item.get("education") or []) if isinstance(e, dict)
    ]

    cur = _current(item)
    return Profile(
        linkedin_url=item.get("linkedinUrl") or fallback_url,
        full_name=full_name,
        headline=item.get("headline") or "",
        about=item.get("about") or "",
        location=location,
        current_company=cur.get("companyName") or "",
        current_company_url=_clean_url(cur.get("companyLinkedinUrl")),
        current_title=cur.get("position") or item.get("headline") or "",
        started_at=None,
        experiences=experiences,
        skills=[s for s in skills if s],
        education=education,
    )
