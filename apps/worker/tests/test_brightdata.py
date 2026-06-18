"""P2 gate (tests.md §6): the Brightdata client returns fixtures with zero
network, retries 429 with bounded backoff, and never logs raw PII."""

from __future__ import annotations

import logging

import pytest
from jill.brightdata import (
    BrightdataNotFound,
    BrightdataRateLimited,
    EmployeeRef,
    Profile,
    get_client,
)
from jill.brightdata.mock import MockBrightdataClient, _slug
from jill.brightdata.retry import call_with_retry
from jill.config import Settings


def _mock_settings(**over) -> Settings:
    base = dict(
        mode="mock", brightdata_api_key="", brightdata_base_url="x",
        bd_dataset_profile="p", bd_dataset_company_people="c", bd_discover_by="company_name",
        bd_poll_timeout=240.0, bd_poll_interval=5.0, bd_stub_retries=1,
        recent_joiner_window_days=90, max_expansion_depth=2,
        max_leads_per_run=50, max_scrapes_per_run=100,
        live_max_companies=1, live_max_depth=0, live_max_leads=8, autoplan=False,
        expand_min_score=40,
        scrape_max_attempts=4, scrape_base_delay=0.5,
        anthropic_api_key="", planner_model="m", scorer_model="m",
        drafter_model="m", triage_model="m",
        webpy_base_url="x", service_token="x",
    )
    base.update(over)
    return Settings(**base)


# --- T6.1 mock fixtures, zero network ---------------------------------------


def test_factory_returns_mock_by_default():
    assert isinstance(get_client(_mock_settings()), MockBrightdataClient)


def test_company_employees_from_fixture():
    client = MockBrightdataClient()
    employees = client.company_employees("Vapi")
    assert len(employees) == 5
    assert all(isinstance(e, EmployeeRef) for e in employees)
    alice = next(e for e in employees if "alice" in e.linkedin_url)
    assert alice.current_company == "Vapi"
    assert alice.started_at == "2026-05-15"


def test_company_lookup_by_url_and_name_match():
    client = MockBrightdataClient()
    by_name = client.company_employees("Retell AI")
    by_url = client.company_employees("https://linkedin.com/company/retell-ai")
    assert [e.linkedin_url for e in by_name] == [e.linkedin_url for e in by_url]


def test_profile_from_fixture_parses_experiences():
    client = MockBrightdataClient()
    prof = client.profile("https://linkedin.com/in/alice-nguyen")
    assert isinstance(prof, Profile)
    assert prof.current_company == "Vapi"
    assert prof.previous_companies() == ["Retell AI", "Twilio", "EchoLabs (acquired)"]
    assert "WebRTC" in prof.skills


def test_network_approximated_via_shared_company():
    client = MockBrightdataClient()
    alice = client.profile("https://linkedin.com/in/alice-nguyen")
    peers = client.network(alice, limit=10)
    urls = {p.linkedin_url for p in peers}
    # Shared-company cohort from Retell AI; excludes Alice herself.
    assert alice.linkedin_url not in urls
    assert "https://linkedin.com/in/frank-li" in urls


def test_missing_fixture_raises_not_found():
    client = MockBrightdataClient()
    with pytest.raises(BrightdataNotFound):
        client.company_employees("NonexistentCorp")


def test_slug_normalization():
    assert _slug("Retell AI") == "retell-ai"
    assert _slug("https://linkedin.com/company/vapi/") == "vapi"
    assert _slug("https://linkedin.com/in/alice-nguyen") == "alice-nguyen"


# --- T6.2 retry / backoff ---------------------------------------------------


def test_retry_succeeds_after_transient_429():
    calls = {"n": 0}
    delays: list[float] = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise BrightdataRateLimited("429")
        return "ok"

    out = call_with_retry(flaky, max_attempts=4, base_delay=0.5,
                          sleep=delays.append)
    assert out == "ok"
    assert calls["n"] == 3
    assert delays == [0.5, 1.0]  # exponential backoff, no real sleeping


def test_retry_gives_up_after_max_attempts():
    calls = {"n": 0}

    def always_429():
        calls["n"] += 1
        raise BrightdataRateLimited("429")

    with pytest.raises(BrightdataRateLimited):
        call_with_retry(always_429, max_attempts=3, base_delay=0.1,
                        sleep=lambda _: None)
    assert calls["n"] == 3  # bounded — exactly max_attempts, no infinite loop


# --- T6.3 no PII in logs ----------------------------------------------------


def test_no_raw_pii_in_logs(caplog):
    client = MockBrightdataClient()
    # Brightdata I/O traces are emitted at DEBUG (INFO is the clean pipeline
    # narrative); the PII contract still applies wherever they're logged.
    with caplog.at_level(logging.DEBUG, logger="jill.brightdata"):
        prof = client.profile("https://linkedin.com/in/alice-nguyen")
        client.network(prof, limit=5)
    text = "\n".join(r.getMessage() for r in caplog.records)
    # Identifiers/counts are fine; raw profile content must not leak.
    assert "alice-nguyen" in text  # the url slug is an identifier, allowed
    for secret in ("WebRTC", "UC Berkeley", "Founding Voice AI Engineer"):
        assert secret not in text


# --- stub / blocked-scrape detection (live mapping) -------------------------


def test_stub_profile_detected():
    """name + current_company but no body = LinkedIn authwall stub, must be caught."""
    from jill.brightdata.live import _is_stub_profile
    stub = {"name": "Frank M", "current_company": {"name": "Punch Financial"}}
    assert _is_stub_profile(stub) is True
    # Any one body field present means it's a real profile.
    assert _is_stub_profile({**stub, "about": "Voice AI engineer"}) is False
    assert _is_stub_profile({**stub, "position": "Staff Engineer"}) is False
    assert _is_stub_profile({**stub, "experience": [{"title": "Eng"}]}) is False


def test_record_flags_surfaces_warning_codes():
    from jill.brightdata.live import _record_flags
    assert _record_flags({"name": "X", "warning_code": "dead_page"}) == {
        "warning_code": "dead_page"
    }
    assert _record_flags({"name": "X"}) == {}  # nothing to surface on a clean row


def test_to_profile_accepts_experiences_plural():
    """Schema-drift guard: the dataset key may be ``experiences`` not ``experience``."""
    from jill.brightdata.live import _to_profile
    rec = {
        "name": "Dana", "position": "Voice AI Engineer",
        "experiences": [{"company": "Vapi", "title": "Founding Engineer"}],
    }
    prof = _to_profile(rec, "https://linkedin.com/in/dana")
    assert len(prof.experiences) == 1
    assert prof.experiences[0].company == "Vapi"
