"""Central config for the worker — env-driven, no magic numbers in flow code (C21).

``mock`` mode (the default) needs zero secrets: Brightdata, the LLM, and outreach
delivery all use fixture/stub implementations. ``live`` mode requires the relevant
API keys and is gated at the edges (the factories raise if a key is missing).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

# Load a repo-root .env (live keys for local dev). No-op if python-dotenv isn't
# installed or no file is found; real deployments use the process environment.
try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
except ModuleNotFoundError:  # pragma: no cover
    pass


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    # mock | live — flips Brightdata, LLM, and outreach between fixtures and real.
    mode: str

    # --- Brightdata ---
    brightdata_api_key: str
    brightdata_base_url: str
    # Dataset ids on the Brightdata Web Scraper API (v3). Defaults are the public
    # LinkedIn datasets; override per-account via env if yours differ.
    bd_dataset_profile: str          # LinkedIn people profile (deep), by URL — collect-by-URL
    # Company info dataset (collect-by-URL). Its record carries an ``employees``
    # list (the members LinkedIn surfaces on the company page) which we use to
    # source candidates from a company — no separate "discover employees" scraper
    # (the standard LinkedIn datasets don't expose one). Seed a company *URL*
    # (linkedin.com/company/<slug>); a bare name is slugified best-effort but may
    # resolve to the wrong org, so prefer the URL.
    bd_dataset_company_people: str
    bd_discover_by: str              # legacy discover field name (unused by the company path)
    bd_poll_timeout: float           # max seconds to wait for an async snapshot
    bd_poll_interval: float          # seconds between snapshot polls
    bd_stub_retries: int             # extra re-scrapes when a profile comes back a
                                     # stub (name only, no body — LinkedIn blocked it)

    # --- pipeline knobs (the bounds that keep the crawl finite) ---
    recent_joiner_window_days: int
    max_expansion_depth: int
    max_leads_per_run: int
    max_scrapes_per_run: int

    # --- live crawl bounds (real scraping is slow + costs money, so a live run
    # from the portal is kept tight; the durable Temporal path does the big crawls)
    live_max_companies: int
    live_max_depth: int
    live_max_leads: int

    # When True, Jill auto-proposes seed companies from the ICP (and replans mid-run
    # if it finds no fit). Off by default: seeds are manual + cross-run-promoted only.
    autoplan: bool

    # Minimum fit score required to expand from a candidate (explore their prev
    # employers / network). Seed-company employees (depth 0) are always explored.
    expand_min_score: int

    # --- scrape retry/backoff ---
    scrape_max_attempts: int
    scrape_base_delay: float

    # --- LLM (Claude); tiered model ids, never hardcoded in logic (C13) ---
    anthropic_api_key: str
    planner_model: str
    scorer_model: str
    drafter_model: str
    triage_model: str

    # --- web-py service API ---
    webpy_base_url: str
    service_token: str

    @property
    def is_live(self) -> bool:
        return self.mode == "live"


@lru_cache
def get_settings() -> Settings:
    return Settings(
        mode=os.environ.get("JILL_MODE", "mock"),
        brightdata_api_key=os.environ.get("BRIGHTDATA_API_KEY", ""),
        brightdata_base_url=os.environ.get(
            "BRIGHTDATA_BASE_URL", "https://api.brightdata.com"
        ),
        bd_dataset_profile=os.environ.get(
            "BRIGHTDATA_DATASET_PROFILE", "gd_l1viktl72bvl7bjuj0"
        ),
        bd_dataset_company_people=os.environ.get(
            "BRIGHTDATA_DATASET_COMPANY_PEOPLE", "gd_l1vikfnt1wgvvqz95w"
        ),
        bd_discover_by=os.environ.get("BRIGHTDATA_DISCOVER_BY", "company_name"),
        bd_poll_timeout=_float("BRIGHTDATA_POLL_TIMEOUT", 240.0),
        bd_poll_interval=_float("BRIGHTDATA_POLL_INTERVAL", 5.0),
        bd_stub_retries=_int("BRIGHTDATA_STUB_RETRIES", 1),
        recent_joiner_window_days=_int("RECENT_JOINER_WINDOW_DAYS", 90),
        max_expansion_depth=_int("MAX_EXPANSION_DEPTH", 2),
        max_leads_per_run=_int("MAX_LEADS_PER_RUN", 50),
        max_scrapes_per_run=_int("MAX_SCRAPES_PER_RUN", 100),
        live_max_companies=_int("JILL_LIVE_MAX_COMPANIES", 1),
        live_max_depth=_int("JILL_LIVE_MAX_DEPTH", 0),
        live_max_leads=_int("JILL_LIVE_MAX_LEADS", 8),
        autoplan=os.environ.get("JILL_AUTOPLAN", "0") == "1",
        expand_min_score=_int("JILL_EXPAND_MIN_SCORE", 40),
        scrape_max_attempts=_int("SCRAPE_MAX_ATTEMPTS", 4),
        scrape_base_delay=_float("SCRAPE_BASE_DELAY", 0.5),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        # Latest Claude models; capable tier for the product-quality steps,
        # cheap tier for triage. Overridable per env.
        planner_model=os.environ.get("JILL_PLANNER_MODEL", "claude-sonnet-4-6"),
        scorer_model=os.environ.get("JILL_SCORER_MODEL", "claude-sonnet-4-6"),
        drafter_model=os.environ.get("JILL_DRAFTER_MODEL", "claude-sonnet-4-6"),
        triage_model=os.environ.get("JILL_TRIAGE_MODEL", "claude-haiku-4-5"),
        webpy_base_url=os.environ.get("WEBPY_BASE_URL", "http://localhost:8000"),
        service_token=os.environ.get("SERVICE_TOKEN", "dev-service-token-change-me"),
    )
