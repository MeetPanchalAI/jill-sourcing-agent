# Jill — the sourcing agent

The "Jill" half of Jack & Jill: an AI recruiter that **finds warm candidates**.
It monitors target companies for **recent joiners**, enriches and scores them
against a role's ICP, expands through each lead's **previous employer + network**,
and stages **consented outreach** (LinkedIn + email) for human approval.

Built on a multi-tenant **Django + Postgres + RLS** core (`web-py`), a durable
**Temporal** worker (`jill`), and **Claude** for fit-scoring and drafting.
Everything runs offline in **mock mode** (no API keys, no LinkedIn scraping).

> One-time setup: `uv` isn't on PATH here, so commands use `python -m uv`.
> ```powershell
> cd C:\Users\meetp\zenerative\web-py
> python -m uv sync --all-packages
> ```

---

## 1. Instant demo — no servers, no setup

```powershell
cd apps\worker
python -m uv run jill demo
```
Runs the whole pipeline in-memory and prints ranked fit leads with provenance.

## 2. Tests

```powershell
# worker (offline; downloads Temporal test server once)
cd apps\worker && python -m uv run pytest -q
# django (needs Postgres)
cd apps\api && docker compose up -d db && cd ..\..
$env:DJANGO_SECRET_KEY='dev-secret'; python -m uv run pytest -q
```

## 3. Full app — API + dashboard

**Terminal A — API + DB:**
```powershell
cd apps\api && docker compose up -d db && cd ..\..
$env:DJANGO_SECRET_KEY='dev-secret'
$env:SERVICE_TOKEN='dev-service-token-change-me'
python -m uv run python apps/api/manage.py migrate
# create a tenant, note the printed id (e.g. 1):
python -m uv run python apps/api/manage.py shell -c "from zenlib.reusable_apps.multitenant.models import Tenant; print(Tenant.objects.create(name='Acme', slug='acme', service_token='x').id)"
python -m uv run python apps/api/manage.py runserver 8000
```

**Terminal B — drive it (same secrets):**
```powershell
cd apps\worker
$env:JILL_TENANT_ID='1'; $env:SERVICE_TOKEN='dev-service-token-change-me'; $env:WEBPY_BASE_URL='http://localhost:8000'
python -m uv run jill health
'{"target_companies":[{"name":"Vapi"}],"must_have_skills":["Python","Realtime Audio"]}' | Out-File icp.json
python -m uv run jill role create --title "Voice AI Engineer" --icp icp.json   # prints a role id
python -m uv run jill source <role_id>      # runs the crawl in-process (--local default)
python -m uv run jill leads <role_id>
```

**Dashboard:** http://localhost:8000/ui/sourcing/?tenant=1 — roles → ranked leads
with provenance → run status (live) → **Approve / Reject** outreach.

## 4. Durable execution (optional — Temporal)

Adds the durable, schedulable, retryable workflow layer. Keep Terminal A running.

```powershell
# Terminal C — Temporal dev server (install: download the CLI, see docs)
temporal server start-dev            # UI at http://localhost:8233

# Terminal D — the Jill worker
cd apps\worker
$env:SERVICE_TOKEN='dev-service-token-change-me'; $env:WEBPY_BASE_URL='http://localhost:8000'
python -m uv run jill worker

# Terminal B — trigger the durable workflow instead of in-process:
python -m uv run jill source <role_id> --no-local
```
Watch the `SourcingRunWorkflow` execute at http://localhost:8233; counters tick
live on the dashboard.

---

## Live mode (real Brightdata / Claude / email)

Mock is the default. To engage the real adapters (key-gated), set `JILL_MODE=live`
plus the relevant keys — `BRIGHTDATA_API_KEY`, `ANTHROPIC_API_KEY`. No code change.

## Layout

```
apps/api/        Django API + dashboard (web-py)
apps/worker/     jill: Brightdata, Claude scorer/drafter, Temporal, CLI
packages/agent/  sourcing domain app (models, API, UI) + mt plumbing
packages/mt/     multitenant + RLS library (do not modify)
docs/sourcing/   prd · constraints · tests · cli · plan
```

## Docs

Planning and reference docs live in [docs/sourcing/](docs/sourcing/): the product
requirements (`prd.md`), the build plan and decisions log (`plan.md`), the
constraints (`constraints.md`), the test plan (`tests.md`), and the CLI reference
(`cli.md`).

Tests: **worker 59 · Django 28**, all green; `ruff` + `bandit` clean; Temporal
replayer-clean.
