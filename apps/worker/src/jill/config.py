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
    # The LinkedIn People-Profiles dataset, queried via the **Dataset Filter API**
    # (not live scraping): filtering by ``current_company_company_id`` returns the
    # full resident profiles (experience/education/about) of a company's people in
    # one job — surfacing *and* deep detail at once. Filtering by ``linkedin_id``
    # fetches a single profile. Override per-account via env if yours differs.
    bd_dataset_profile: str
    bd_company_records_limit: int    # max profiles to pull per company filter (cost cap)
    bd_poll_timeout: float           # max seconds to wait for an async filter snapshot
    bd_poll_interval: float          # seconds between snapshot polls

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
    # Skip companies/candidates already handled in an earlier workflow. Off for now
    # (rescans allowed — a re-run re-scans its seeds and re-evaluates) — a later
    # optimization once the iterative flow is dialed in. Enable: JILL_CROSS_RUN_DEDUP=1.
    cross_run_dedup: bool
    # Also expand through a candidate's network (people_also_viewed). On by default;
    # set JILL_EXPAND_NETWORK=0 to force prev-employer-only expansion.
    expand_network: bool

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
        bd_company_records_limit=_int("BRIGHTDATA_COMPANY_LIMIT", 25),
        bd_poll_timeout=_float("BRIGHTDATA_POLL_TIMEOUT", 300.0),
        bd_poll_interval=_float("BRIGHTDATA_POLL_INTERVAL", 5.0),
        recent_joiner_window_days=_int("RECENT_JOINER_WINDOW_DAYS", 90),
        max_expansion_depth=_int("MAX_EXPANSION_DEPTH", 2),
        max_leads_per_run=_int("MAX_LEADS_PER_RUN", 50),
        max_scrapes_per_run=_int("MAX_SCRAPES_PER_RUN", 100),
        live_max_companies=_int("JILL_LIVE_MAX_COMPANIES", 1),
        live_max_depth=_int("JILL_LIVE_MAX_DEPTH", 0),
        live_max_leads=_int("JILL_LIVE_MAX_LEADS", 8),
        autoplan=os.environ.get("JILL_AUTOPLAN", "0") == "1",
        expand_min_score=_int("JILL_EXPAND_MIN_SCORE", 40),
        cross_run_dedup=os.environ.get("JILL_CROSS_RUN_DEDUP", "0") == "1",
        expand_network=os.environ.get("JILL_EXPAND_NETWORK", "1") == "1",
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
