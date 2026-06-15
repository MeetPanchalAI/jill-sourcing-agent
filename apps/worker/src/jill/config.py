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

    # --- pipeline knobs (the bounds that keep the crawl finite) ---
    recent_joiner_window_days: int
    max_expansion_depth: int
    max_leads_per_run: int
    max_scrapes_per_run: int

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
        recent_joiner_window_days=_int("RECENT_JOINER_WINDOW_DAYS", 90),
        max_expansion_depth=_int("MAX_EXPANSION_DEPTH", 2),
        max_leads_per_run=_int("MAX_LEADS_PER_RUN", 50),
        max_scrapes_per_run=_int("MAX_SCRAPES_PER_RUN", 100),
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
