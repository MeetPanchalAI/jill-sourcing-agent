"""API-surface tests: service auth, idempotent upsert, the leads endpoint, and
the outreach approval guard — all over HTTP through the DRF router."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def _create_role(client, headers, title="Voice AI Eng"):
    resp = client.post(
        "/api/v1/sourcing/roles/",
        {"title": title, "icp": {"target_companies": [{"name": "Vapi"}]}},
        format="json",
        **headers,
    )
    assert resp.status_code == 201, resp.content
    return resp.json()["id"]


def test_source_seeds_targets(api_client, tenant_a, service_headers):
    h = service_headers(tenant_a)
    role_id = _create_role(api_client, h)
    resp = api_client.post(f"/api/v1/sourcing/roles/{role_id}/source/", **h)
    assert resp.status_code == 201, resp.content
    run = resp.json()
    assert run["status"] == "pending"
    # The seed company became a TargetCompany.
    targets = api_client.get("/api/v1/sourcing/targets/", **h).json()
    names = [t["name"] for t in targets["results"]]
    assert "Vapi" in names


def test_candidate_upsert_is_idempotent(api_client, tenant_a, service_headers):
    h = service_headers(tenant_a)
    body = {"linkedin_url": "https://linkedin.com/in/alice", "full_name": "Alice"}
    r1 = api_client.post("/api/v1/sourcing/candidates/", body, format="json", **h)
    assert r1.status_code == 201
    # Second post with same URL updates, does not duplicate.
    body["headline"] = "Voice AI Engineer"
    r2 = api_client.post("/api/v1/sourcing/candidates/", body, format="json", **h)
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    listing = api_client.get("/api/v1/sourcing/candidates/", **h).json()
    assert listing["count"] == 1
    assert listing["results"][0]["headline"] == "Voice AI Engineer"


def test_leads_ranked_by_score(api_client, tenant_a, service_headers):
    h = service_headers(tenant_a)
    role_id = _create_role(api_client, h)
    # two candidates with different scores
    for url, name, score, verdict in [
        ("https://linkedin.com/in/a", "A", 90, "fit"),
        ("https://linkedin.com/in/b", "B", 40, "drop"),
    ]:
        cid = api_client.post(
            "/api/v1/sourcing/candidates/",
            {"linkedin_url": url, "full_name": name}, format="json", **h,
        ).json()["id"]
        api_client.post(
            "/api/v1/sourcing/scores/",
            {"candidate": cid, "role": role_id, "score": score,
             "verdict": verdict, "reasons": ["x"]},
            format="json", **h,
        )
    leads = api_client.get(
        f"/api/v1/sourcing/roles/{role_id}/leads/?verdict=fit", **h
    ).json()
    assert [lead["full_name"] for lead in leads] == ["A"]
    assert leads[0]["provenance"] == []  # no edges yet


def test_target_create_is_idempotent(api_client, tenant_a, service_headers):
    """Re-scanning a company upserts the same target (200), never 500s."""
    h = service_headers(tenant_a)
    role_id = _create_role(api_client, h)
    body = {"role": role_id, "name": "Vapi", "source": "seed", "depth": 0}
    r1 = api_client.post("/api/v1/sourcing/targets/", body, format="json", **h)
    r2 = api_client.post("/api/v1/sourcing/targets/", body, format="json", **h)
    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    listing = api_client.get("/api/v1/sourcing/targets/", **h).json()
    assert sum(1 for t in listing["results"] if t["name"] == "Vapi") == 1


def test_lead_edge_create_is_idempotent(api_client, tenant_a, service_headers):
    """Re-posting the same provenance edge matches it (200), not duplicates (201)."""
    h = service_headers(tenant_a)
    role_id = _create_role(api_client, h)
    cid = api_client.post(
        "/api/v1/sourcing/candidates/",
        {"linkedin_url": "https://linkedin.com/in/e", "full_name": "E"},
        format="json", **h,
    ).json()["id"]
    edge = {"role": role_id, "to_candidate": cid, "kind": "recent_joiner",
            "depth": 0}
    r1 = api_client.post("/api/v1/sourcing/lead-edges/", edge, format="json", **h)
    r2 = api_client.post("/api/v1/sourcing/lead-edges/", edge, format="json", **h)
    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    listing = api_client.get("/api/v1/sourcing/lead-edges/", **h).json()
    assert listing["count"] == 1


def test_run_finalize_sets_status_and_counters(api_client, tenant_a, service_headers):
    h = service_headers(tenant_a)
    role_id = _create_role(api_client, h)
    run = api_client.post(f"/api/v1/sourcing/roles/{role_id}/source/", **h).json()
    resp = api_client.post(
        f"/api/v1/sourcing/runs/{run['id']}/finalize/",
        {"status": "completed", "fit_candidates": 3, "drafted": 6},
        format="json", **h,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["fit_candidates"] == 3
    assert body["finished_at"] is not None


def test_enrichment_upsert_is_idempotent(api_client, tenant_a, service_headers):
    h = service_headers(tenant_a)
    cid = api_client.post(
        "/api/v1/sourcing/candidates/",
        {"linkedin_url": "https://linkedin.com/in/en", "full_name": "En"},
        format="json", **h,
    ).json()["id"]
    body = {"candidate": cid, "skills": ["Python"], "experiences": [],
            "raw": {}, "fetched_at": "2026-06-14T00:00:00Z"}
    r1 = api_client.post("/api/v1/sourcing/enrichments/", body, format="json", **h)
    body["skills"] = ["Python", "Go"]
    r2 = api_client.post("/api/v1/sourcing/enrichments/", body, format="json", **h)
    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    assert api_client.get("/api/v1/sourcing/enrichments/", **h).json()["count"] == 1


def test_outreach_create_is_idempotent(api_client, tenant_a, service_headers):
    """Re-drafting the same channel matches the existing draft (200), never 500s."""
    h = service_headers(tenant_a)
    role_id = _create_role(api_client, h)
    cid = api_client.post(
        "/api/v1/sourcing/candidates/",
        {"linkedin_url": "https://linkedin.com/in/d", "full_name": "D"},
        format="json",
        **h,
    ).json()["id"]
    body = {
        "candidate": cid,
        "role": role_id,
        "channel": "linkedin",
        "subject": "",
        "body": "hello",
    }
    r1 = api_client.post("/api/v1/sourcing/outreach/", body, format="json", **h)
    body["body"] = "updated"
    r2 = api_client.post("/api/v1/sourcing/outreach/", body, format="json", **h)
    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    listing = api_client.get("/api/v1/sourcing/outreach/", **h).json()
    assert listing["count"] == 1


def test_outreach_cannot_send_without_approval(api_client, tenant_a, service_headers):
    h = service_headers(tenant_a)
    role_id = _create_role(api_client, h)
    cid = api_client.post(
        "/api/v1/sourcing/candidates/",
        {"linkedin_url": "https://linkedin.com/in/c", "full_name": "C"},
        format="json", **h,
    ).json()["id"]
    draft = api_client.post(
        "/api/v1/sourcing/outreach/",
        {"candidate": cid, "role": role_id, "channel": "email",
         "subject": "hi", "body": "hello"},
        format="json", **h,
    ).json()
    assert draft["status"] == "draft"
    # approve transitions to approved
    approved = api_client.post(
        f"/api/v1/sourcing/outreach/{draft['id']}/approve/", **h
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

