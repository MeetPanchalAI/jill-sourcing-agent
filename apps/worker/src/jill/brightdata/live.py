"""Live LinkedIn data client — Brightdata **Dataset Filter API**.

Instead of scraping LinkedIn live (which now returns login-walled stubs), we query
Brightdata's resident People-Profiles dataset by filter, which returns *complete*
profiles (experience / education / about / people_also_viewed):

* ``company_employees(url)`` → filter ``current_company_company_id = <slug>`` →
  every resident profile at that company, in one job. This surfaces the people
  *and* gives the deep detail the rubric scores on (no separate enrichment step —
  this is what replaces Apify). The full profiles are cached so the pipeline's
  per-candidate ``profile()`` call is served from memory, not a second query.
* ``profile(url)``           → cache hit, else filter ``linkedin_id = <slug>``.
* ``network(profile)``       → related people via the record's ``people_also_viewed``.

A filter job is async (start → poll the snapshot download until ready, ≤5 min).
Dataset id + per-company record cap come from ``Settings`` (env-overridable).
Identifiers + counts are logged, never profile text (C4).
"""

from __future__ import annotations

import html
import logging
import re
import time

import httpx

from ..config import Settings
from .base import BrightdataClient
from .errors import (
    BrightdataAuthError,
    BrightdataError,
    BrightdataNotFound,
    BrightdataRateLimited,
    BrightdataServerError,
)
from .types import EmployeeRef, Experience, Profile

logger = logging.getLogger("jill.brightdata")

_FILTER = "/datasets/filter"
_DOWNLOAD = "/datasets/snapshots/{sid}/download"


class LiveBrightdataClient(BrightdataClient):
    network_method = "people_also_viewed"

    def __init__(self, settings: Settings):
        if not settings.brightdata_api_key:
            raise BrightdataAuthError("BRIGHTDATA_API_KEY required for live mode")
        self._s = settings
        self._http = httpx.Client(
            base_url=settings.brightdata_base_url,
            headers={
                "Authorization": f"Bearer {settings.brightdata_api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
        # Full profiles fetched by a company filter are cached here, so the
        # pipeline's per-candidate profile() lookup is free (no second query).
        self._cache: dict[str, Profile] = {}

    # --- transport -------------------------------------------------------

    def _raise_for_status(self, resp: httpx.Response, where: str) -> None:
        if resp.status_code in (401, 403):
            raise BrightdataAuthError(f"{where}: {resp.status_code} (check token)")
        if resp.status_code == 429:
            raise BrightdataRateLimited(where)
        if resp.status_code == 404:
            raise BrightdataNotFound(where)
        if resp.status_code >= 500:
            raise BrightdataServerError(f"{where}: {resp.status_code}")
        if resp.status_code >= 400:
            # e.g. a dataset with no discovery collector → "does not support
            # collection". Terminal and specific — surface the message.
            raise BrightdataError(f"{where}: {resp.status_code} {resp.text[:160]}")
        resp.raise_for_status()

    def _filter(self, field: str, value: str, records_limit: int,
                *, operator: str = "=") -> list[dict]:
        """Filter the profiles dataset (field/operator/value) → resident records.

        Starts the async filter job, then polls its snapshot download until ready."""
        body = {
            "dataset_id": self._s.bd_dataset_profile,
            "records_limit": records_limit,
            "filter": {"name": field, "operator": operator, "value": value},
        }
        logger.debug("brightdata.filter %s %s %r limit=%d", field, operator, value,
                     records_limit)
        resp = self._http.post(_FILTER, json=body)
        self._raise_for_status(resp, "filter")
        sid = (resp.json() or {}).get("snapshot_id")
        if not sid:
            raise BrightdataError(f"filter returned no snapshot_id: {resp.text[:200]}")
        return self._download(sid)

    def _download(self, sid: str) -> list[dict]:
        """Poll a filter snapshot's download until it's built, then return records.

        While building, the endpoint replies either ``202 Snapshot is building`` or
        ``400 {"error":"Snapshot not ready"}`` — both mean *retry*, not a failure."""
        deadline = time.monotonic() + self._s.bd_poll_timeout
        while True:
            resp = self._http.get(_DOWNLOAD.format(sid=sid), params={"format": "json"})
            body = resp.text
            building = resp.status_code == 202 or (
                resp.status_code >= 400
                and ("not ready" in body.lower() or "building" in body.lower())
            )
            if building:
                if time.monotonic() >= deadline:
                    raise BrightdataError(
                        f"filter snapshot {sid} not ready after "
                        f"{self._s.bd_poll_timeout:.0f}s")
                logger.debug("brightdata.filter snapshot=%s building ...", sid)
                time.sleep(self._s.bd_poll_interval)
                continue
            self._raise_for_status(resp, "snapshot-download")
            records = resp.json()
            if isinstance(records, dict):  # some shapes wrap rows under a key
                records = records.get("data") or records.get("results") or []
            logger.debug("brightdata.filter snapshot=%s -> %d records", sid, len(records))
            return records

    # --- interface -------------------------------------------------------

    def company_employees(self, company: str) -> list[EmployeeRef]:
        """Every resident profile at ``company`` (a LinkedIn company URL), via the
        Filter API on ``current_company_company_id``. Each record is a *full*
        profile, so we cache them for the pipeline's later ``profile()`` calls and
        return shallow refs for the surfacing stage."""
        slug = _company_slug(company)
        if not slug:
            raise BrightdataNotFound(f"no company slug in {company!r}")
        records = self._filter(
            "current_company_company_id", slug, self._s.bd_company_records_limit
        )
        seen: set[str] = set()
        employees: list[EmployeeRef] = []
        for r in records:
            prof = _to_profile(r, r.get("url") or "")
            url = _clean_profile_url(prof.linkedin_url)
            if not url or url in seen:
                continue
            seen.add(url)
            self._cache[url] = prof  # serve the later profile() call from memory
            employees.append(EmployeeRef(
                linkedin_url=url, full_name=prof.full_name, headline=prof.headline,
                current_title=prof.current_title, current_company=prof.current_company,
                location=prof.location,
            ))
        logger.debug("brightdata.company_employees slug=%s -> %d profiles",
                     slug, len(employees))
        if not employees:
            raise BrightdataNotFound(f"no resident profiles at company {slug!r}")
        return employees

    def profile(self, linkedin_url: str) -> Profile:
        """Full profile for a URL — from the company-filter cache when available,
        else a single-profile filter on ``linkedin_id``."""
        cached = self._cache.get(_clean_profile_url(linkedin_url))
        if cached is not None:
            return cached
        slug = _profile_slug(linkedin_url)
        records = self._filter("linkedin_id", slug, 1) if slug else []
        if not records:
            raise BrightdataNotFound(linkedin_url)
        prof = _to_profile(records[0], linkedin_url)
        self._cache[_clean_profile_url(linkedin_url)] = prof
        logger.debug("brightdata.profile %s -> %d experiences, %d skills",
                     linkedin_url, len(prof.experiences), len(prof.skills))
        return prof

    def network(self, profile: Profile, limit: int = 10) -> list[EmployeeRef]:
        """Network-expansion edge via LinkedIn's ``people_also_viewed`` — the one
        related-people signal Brightdata reliably exposes (the company-employee
        widget is too thin/biased to source from). Each entry already carries
        name + company (``about``) + profile URL, so peers can be pre-triaged on
        that metadata before we spend a scrape enriching them."""
        seen: set[str] = {profile.linkedin_url}
        peers: list[EmployeeRef] = []
        for p in profile.people_also_viewed:
            url = _clean_profile_url(p.get("profile_link") or p.get("url"))
            if not url or "/in/" not in url or url in seen:
                continue
            seen.add(url)
            peers.append(EmployeeRef(
                linkedin_url=url,
                full_name=p.get("name") or "",
                # ``about`` here is the person's current company/school — a cheap
                # relevance signal for triage before enrichment. Coerce None → ""
                # (the field can be present-but-null in the source).
                headline=p.get("about") or "",
                current_company=p.get("about") or "",
                location=p.get("location") or "",
            ))
            if len(peers) >= limit:
                break
        logger.debug("brightdata.network %s -> %d peers (people_also_viewed)",
                     profile.linkedin_url, len(peers))
        return peers


# --- field mapping (Brightdata records → our wire types) -----------------
# Real records carry many more fields than we model and use varied key names;
# map defensively and ignore the rest.

def _company_slug(company: str) -> str:
    """The ``current_company_company_id`` to filter on, from a company URL/name.
    ``linkedin.com/company/vapi-ai/`` → ``vapi-ai``; a bare name is slugified."""
    c = (company or "").strip()
    if "linkedin.com/company/" in c:
        return c.split("linkedin.com/company/", 1)[1].split("?")[0].strip("/").split("/")[0]
    return re.sub(r"[^a-z0-9]+", "-", c.lower()).strip("-")


def _profile_slug(url: str) -> str:
    """The ``linkedin_id`` to filter on, from a profile URL: ``…/in/<slug>`` → slug."""
    u = (url or "").split("?")[0].rstrip("/")
    return u.split("/in/", 1)[1].split("/")[0] if "/in/" in u else ""


def _clean_profile_url(link: str | None) -> str:
    """Strip LinkedIn's tracking query (``?trk=org-employees``) off a member link."""
    return (link or "").split("?")[0].strip()


_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    """LinkedIn descriptions arrive as HTML (``<br>``, ``&quot;`` …). Turn them into
    plain text the scorer can read: tags → spaces, entities unescaped, collapsed."""
    if not s:
        return ""
    text = html.unescape(_TAG.sub(" ", s))
    return re.sub(r"\s+", " ", text).strip()


def _first(d: dict, *keys: str, default=""):
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return default


def _end_or_none(value):
    """A current role's end is null or the literal ``"Present"``; normalize both to
    None so ``previous_companies`` treats the role as ongoing, not past."""
    if not value or str(value).strip().lower() in ("present", "current"):
        return None
    return value


def _company_name(value) -> str:
    if isinstance(value, dict):
        return value.get("name") or value.get("company_name") or ""
    return value or ""


def _company_field(value, key: str) -> str:
    return value.get(key) or "" if isinstance(value, dict) else ""


def _company_url_of(cc) -> str:
    """Canonical LinkedIn company URL from a profile's ``current_company`` dict —
    prefer its ``link``, else build one from ``company_id``. Used to promote a
    candidate's company into the next workflow's seed set (keyed by URL, not the
    ambiguous display name)."""
    if not isinstance(cc, dict):
        return ""
    link = _clean_profile_url(cc.get("link"))
    if "linkedin.com/company/" in link:
        return link
    cid = cc.get("company_id")
    return f"https://www.linkedin.com/company/{cid}" if cid else ""


def _to_profile(r: dict, fallback_url: str) -> Profile:
    experiences = []
    for e in (r.get("experience") or r.get("experiences") or []):
        if not isinstance(e, dict):
            continue
        company = _company_name(_first(e, "company", "company_name", default={}))
        cid = e.get("company_id")
        company_url = f"https://www.linkedin.com/company/{cid}" if cid else ""
        # An entry is either a single role, or a company with nested ``positions``
        # (multiple roles). Flatten to one Experience per role so each role's dates
        # and description (what they did — our only skills signal) are preserved.
        positions = e.get("positions") if isinstance(e.get("positions"), list) else None
        rows = positions or [e]
        for pos in rows:
            if not isinstance(pos, dict):
                continue
            experiences.append(Experience(
                company=company,
                company_url=company_url,
                title=_first(pos, "title", "position"),
                start=_first(pos, "start_date", "start", default=None) or None,
                end=_end_or_none(_first(pos, "end_date", "end", default="")),
                description=_strip_html(_first(pos, "description", "description_html",
                                               "summary", default="")),
            ))
    skills = r.get("skills") or []
    if skills and isinstance(skills[0], dict):
        skills = [s.get("name", "") for s in skills if s.get("name")]
    cc = _first(r, "current_company", "company", default={})
    return Profile(
        linkedin_url=_first(r, "url", "input_url", default=fallback_url),
        full_name=_first(r, "name", "full_name"),
        # ``position`` is the current-role headline; fall back to the current
        # company's title (the dataset often carries the title only there).
        headline=_first(r, "headline", "position", "current_position") or _company_field(cc, "title"),
        # The richest free-text signal LinkedIn returns — domain, stack, what they
        # build. The scorer reads it, so a sparse skills/experience list still scores.
        about=_first(r, "about", "summary", "bio"),
        location=_first(r, "location", "city", "country"),
        current_company=_company_name(cc),
        current_company_url=_company_url_of(cc),
        current_title=_first(r, "position", "current_position", "current_title") or _company_field(cc, "title"),
        started_at=_first(r, "current_company_join_date", default=None) or None,
        experiences=experiences,
        skills=[s for s in skills if isinstance(s, str)],
        education=r.get("education") or [],
        people_also_viewed=r.get("people_also_viewed") or [],
    )
