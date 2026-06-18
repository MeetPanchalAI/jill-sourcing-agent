"""Live LinkedIn data client.

Brightdata is the *surfacing* layer (trigger → poll → download a snapshot):

* ``company_employees(url)`` → the members LinkedIn lists on a company page.
* ``network(profile)``       → a related-people cohort via ``people_also_viewed``.

Profiles are *enriched* through Apify (``jill.enrich``) when an ``APIFY_TOKEN`` is
set — Brightdata returns only name/title/recent-school, too thin for the rubric.
If Apify is unavailable (run quota, un-approved actor), ``profile()`` falls back to
the Brightdata profile dataset (shallow) so a run still completes.

Dataset ids come from ``Settings`` (env-overridable). Every request, snapshot
status, and record count is logged (identifiers + counts, never profile text, C4);
real scrapes are slow and cost money, so live runs are tightly bounded
(see ``config.live_max_*``).
"""

from __future__ import annotations

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

_TRIGGER = "/datasets/v3/trigger"
_SNAPSHOT = "/datasets/v3/snapshot/{sid}"
_PROGRESS = "/datasets/v3/progress/{sid}"


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
        # Deep profile enrichment goes through Apify when a key is set — Brightdata
        # only gives name+title+recent school, too thin for the rubric. Falls back
        # to the Brightdata profile dataset when no Apify key is configured.
        self._apify = None
        if settings.apify_api_key:
            from ..enrich import ApifyEnricher
            self._apify = ApifyEnricher(settings)
            logger.info("profile enrichment: Apify (actor=%s)", settings.apify_profile_actor)

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

    def _trigger(self, dataset_id: str, rows: list[dict]) -> str:
        """Start a collect-by-URL collection; return its snapshot id."""
        params = {"dataset_id": dataset_id, "include_errors": "true"}
        logger.debug("brightdata.trigger dataset=%s rows=%d", dataset_id, len(rows))
        resp = self._http.post(_TRIGGER, params=params, json=rows)
        self._raise_for_status(resp, "trigger")
        sid = resp.json().get("snapshot_id")
        if not sid:
            raise BrightdataError(f"trigger returned no snapshot_id: {resp.text[:200]}")
        logger.debug("brightdata.trigger -> snapshot=%s", sid)
        return sid

    def _poll(self, sid: str) -> list[dict]:
        """Poll a snapshot until ready, then return its records."""
        deadline = time.monotonic() + self._s.bd_poll_timeout
        waited = 0.0
        while True:
            resp = self._http.get(_PROGRESS.format(sid=sid))
            self._raise_for_status(resp, "progress")
            status = resp.json().get("status", "unknown")
            if status == "ready":
                break
            if status == "failed":
                raise BrightdataError(f"snapshot {sid} failed")
            if time.monotonic() >= deadline:
                raise BrightdataError(
                    f"snapshot {sid} not ready after {self._s.bd_poll_timeout:.0f}s "
                    f"(last status={status})"
                )
            logger.debug("brightdata.poll snapshot=%s status=%s (waited %.0fs)",
                        sid, status, waited)
            time.sleep(self._s.bd_poll_interval)
            waited += self._s.bd_poll_interval

        data = self._http.get(_SNAPSHOT.format(sid=sid), params={"format": "json"})
        self._raise_for_status(data, "snapshot")
        records = data.json()
        if isinstance(records, dict):  # some datasets wrap rows under a key
            records = records.get("data") or records.get("results") or []
        logger.debug("brightdata.snapshot=%s ready -> %d records", sid, len(records))
        return records

    def _collect(self, dataset_id: str, rows: list[dict]) -> list[dict]:
        return self._poll(self._trigger(dataset_id, rows))

    # --- interface -------------------------------------------------------

    def company_employees(self, company: str) -> list[EmployeeRef]:
        """Source candidates from a company via the company-info dataset.

        Brightdata's standard LinkedIn datasets expose no "discover employees by
        company" scraper, but the company page record carries an ``employees``
        list (the members LinkedIn surfaces publicly — a sample, not the full
        headcount). We collect the company by URL and return those members as
        shallow refs; the pipeline enriches + scores each for role fit.

        ``company`` should be a LinkedIn company URL; a bare name is slugified
        best-effort (and may resolve to the wrong org — prefer the URL)."""
        url = _to_company_url(company)
        if "linkedin.com/company/" not in (company or ""):
            logger.warning("[company_employees] %r is a bare name; guessing %s — "
                           "this may resolve to the wrong org. Seed the LinkedIn "
                           "company URL to be sure.", company, url)
        records = self._collect(self._s.bd_dataset_company_people, [{"url": url}])
        if not records:
            raise BrightdataNotFound(f"no LinkedIn company page for {company!r} ({url})")
        rec = records[0]
        company_name = rec.get("name") or company
        seen: set[str] = set()
        employees: list[EmployeeRef] = []
        for m in rec.get("employees") or []:
            link = _clean_profile_url(m.get("link"))
            if not link or "/in/" not in link or link in seen:
                continue
            seen.add(link)
            employees.append(EmployeeRef(
                linkedin_url=link,
                full_name=m.get("title", ""),
                current_company=company_name,
            ))
        logger.debug("brightdata.company_employees %r (%s) -> %d listed members "
                    "(of %s on LinkedIn)", company, url, len(employees),
                    rec.get("employees_in_linkedin"))
        if not employees:
            raise BrightdataNotFound(
                f"no public members listed on {company_name!r} company page ({url})"
            )
        return employees

    def profile(self, linkedin_url: str) -> Profile:
        # Deep details from Apify (rich experience/skills/education the rubric
        # needs). If Apify is unavailable for an account-level reason (run quota,
        # un-approved actor, server error), don't fail the whole run — log it
        # loudly and fall back to the Brightdata profile (shallow). Full
        # enrichment resumes automatically once Apify is restored/upgraded.
        if self._apify is not None:
            try:
                return self._apify.profile(linkedin_url)
            except BrightdataNotFound:
                raise  # genuinely no profile → skip this candidate
            except Exception as exc:
                logger.warning(
                    "apify enrichment unavailable (%s) — falling back to the "
                    "Brightdata profile (shallow; upgrade Apify for full detail)",
                    str(exc)[:180],
                )
        record = self._collect_profile(linkedin_url)
        prof = _to_profile(record, linkedin_url)
        logger.debug("brightdata.profile %s -> %d experiences, %d skills",
                    linkedin_url, len(prof.experiences), len(prof.skills))
        return prof

    def _collect_profile(self, url: str) -> dict:
        """Scrape one profile, re-scraping if Brightdata returns a *stub*.

        A stub carries identity (name + current company) but no profile body —
        no role, summary, or experience. It's what LinkedIn's anti-bot / authwall
        returns when the live page can't be fully rendered, and Brightdata bills
        it like a success. Retrying often clears a transient block; if it doesn't,
        we raise ``BrightdataNotFound`` so the lead is skipped rather than scored
        as an (empty, guaranteed-to-DROP) profile. The record's field *names* and
        any warning/error codes are logged — never the profile text (C4)."""
        attempts = 1 + max(0, self._s.bd_stub_retries)
        for attempt in range(1, attempts + 1):
            records = self._collect(self._s.bd_dataset_profile, [{"url": url}])
            if not records:
                raise BrightdataNotFound(url)
            rec = records[0]
            if not _is_stub_profile(rec):
                return rec
            logger.warning(
                "brightdata.profile STUB url=%s attempt=%d/%d keys=%s flags=%s",
                url, attempt, attempts, sorted(rec.keys()),
                _record_flags(rec) or "none",
            )
            if attempt < attempts:
                time.sleep(self._s.bd_poll_interval)
        raise BrightdataNotFound(
            f"{url}: profile body empty after {attempts} attempt(s) — LinkedIn "
            f"returned no public experience/about (blocked or private)"
        )

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


# --- record health (detect blocked / partial scrapes) --------------------
# Brightdata bills every returned row, including the stubs LinkedIn's authwall
# yields. We detect those so they're retried + skipped, not scored as empty.

_FLAG_KEYS = ("warning", "warning_code", "error", "error_code")


def _record_flags(r: dict) -> dict:
    """Brightdata's warning/error markers on a record. Codes, not profile text —
    safe to log under C4, and the fastest signal for *why* a scrape came back thin."""
    return {k: r[k] for k in _FLAG_KEYS if r.get(k)}


def _is_stub_profile(r: dict) -> bool:
    """True when a record has no profile *body* — no role, summary, or experience.
    Identity fields (name/current_company) alone don't count: those survive an
    authwall hit while the substance we score on does not."""
    has_body = bool(
        _first(r, "about", "summary", "bio")
        or _first(r, "position", "current_position", "current_title", "title")
        or r.get("experience") or r.get("experiences")
    )
    return not has_body


# --- field mapping (Brightdata records → our wire types) -----------------
# Real records carry many more fields than we model and use varied key names;
# map defensively and ignore the rest.

def _to_company_url(company: str) -> str:
    """A LinkedIn company URL for ``company``: passed through if already a company
    URL, else slugified from the name (best-effort — may hit the wrong org)."""
    c = (company or "").strip()
    if "linkedin.com/company/" in c:
        return c if c.startswith("http") else f"https://{c}"
    slug = re.sub(r"[^a-z0-9]+", "-", c.lower()).strip("-")
    return f"https://www.linkedin.com/company/{slug}"


def _clean_profile_url(link: str | None) -> str:
    """Strip LinkedIn's tracking query (``?trk=org-employees``) off a member link."""
    return (link or "").split("?")[0].strip()


def _first(d: dict, *keys: str, default=""):
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return default


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
        experiences.append(Experience(
            company=_company_name(_first(e, "company", "company_name", default={})),
            title=_first(e, "title", "position"),
            start=_first(e, "start_date", "start", default=None) or None,
            end=_first(e, "end_date", "end", default=None) or None,
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
