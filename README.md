# Jill — AI Sourcing Agent

An AI recruiting agent that **finds warm candidates**. Jill monitors target
companies for recent joiners, scores each against a role's ideal-candidate profile,
expands the search through every strong lead's **previous employer and network**,
and stages personalized outreach for a human to approve — never auto-sent.

The insight: people who *recently joined a relevant company* are warm leads, and
their former colleagues and previous employers are dense pools of similar talent.
So the core loop is **lead → lead's previous employer → lead's network**, bounded by
depth and budget.

> Everything runs **offline by default** (fixtures, no API keys, no real scraping).
> Switching to real services is a single configuration flag.

---

## Highlights

- **Multi-tenant by construction** — Postgres **row-level security** isolates every
  tenant's data at the database layer, not just in application code.
- **Durable orchestration** — the crawl runs as a **Temporal** workflow: automatic
  retries, exact resume-after-crash, deterministic replay, and cron scheduling.
- **Structured AI judgment** — Claude is used only where it adds value (scoring fit,
  drafting outreach), always behind a validated schema with grounded evidence.
- **Weighted rubric scoring** — ex-founder, school pedigree, language, domain
  experience, tenure band, and open-ended signals; each candidate gets a
  per-criterion breakdown and a one-line summary.
- **Consent-first outreach** — a state machine forbids sending without explicit
  approval; one-click *approve-and-send* through a connected LinkedIn account.
- **Operator surface** — a server-rendered dashboard (ranked leads, rubric chips,
  live run status, spend estimate) and a `jill` command-line tool.
- **Tested** — worker 59 + Django 28 tests, lint and security scans clean, and a
  Temporal replay-determinism check.

---

## Architecture

Three cleanly separated planes:

```
Record plane    Django + DRF + Postgres (row-level security)
                The system of record + a service-token HTTP API + the dashboard.
      ▲  writes over HTTP (the X-Tenant-Id header drives isolation)
      │
Control plane   Temporal worker — a bounded breadth-first crawl of the lead graph
      ▲  calls
      │
Judgment plane  Claude, behind interfaces — score a candidate, draft an invite
```

The worker authenticates as a **service account** and only ever writes through the
tenant-scoped API, so row-level security stays the single isolation boundary. Every
external dependency (LinkedIn data, the language model, outreach delivery) sits
behind an interface with a **mock** and a **live** implementation, selected at
runtime — which is why the whole system runs deterministically offline.

---

## Tech stack

| Layer | Technology |
| --- | --- |
| API & web | Python 3.12, Django 5.2, Django REST Framework |
| Database | PostgreSQL 17 (row-level security) |
| Orchestration | Temporal |
| AI | Anthropic Claude (via the official SDK), Pydantic-validated outputs |
| LinkedIn data | Brightdata (mockable) |
| Tooling | `uv` (workspace + deps), `ruff` (lint), `bandit` (security), `pytest` |

---

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — the package & workspace manager
  (`curl -LsSf https://astral.sh/uv/install.sh | sh`, or `pip install uv`)
- **Docker** — to run PostgreSQL locally

No environment variables are required for local development — every setting has a
sensible default.

---

## Quickstart

```bash
git clone https://github.com/MeetPanchalAI/jill-sourcing-agent.git
cd jill-sourcing-agent
uv sync --all-packages          # install all workspace packages
```

### 1. See it run — no servers, no setup

```bash
uv run jill demo
```

Runs the full pipeline in memory over fixtures and prints the ranked, rubric-scored
leads with provenance.

### 2. Run the full application (API + dashboard)

```bash
# start PostgreSQL
docker compose -f apps/api/docker-compose.yml up -d db

# apply migrations
uv run python apps/api/manage.py migrate

# create a tenant (prints its id, e.g. 1)
uv run python apps/api/manage.py shell -c \
  "from zenlib.reusable_apps.multitenant.models import Tenant; \
   print(Tenant.objects.create(name='Acme', slug='acme', service_token='x').id)"

# run the API + dashboard
uv run python apps/api/manage.py runserver 8000
```

In a second terminal, drive it with the CLI (replace `1` with your tenant id):

```bash
echo '{"target_companies":[{"name":"Vapi"}],"must_have_skills":["Python","Realtime Audio"]}' > icp.json
JILL_TENANT_ID=1 uv run jill role create --title "Voice AI Engineer" --icp icp.json
JILL_TENANT_ID=1 uv run jill source 1     # runs the crawl in-process
JILL_TENANT_ID=1 uv run jill leads 1
```

Open the dashboard: **http://localhost:8000/ui/sourcing/?tenant=1** — ranked leads
with rubric chips and provenance, live run status, spend, and one-click approve.

> **Windows PowerShell:** set inline environment variables with
> `$env:JILL_TENANT_ID='1'` on a separate line instead of the `NAME=value cmd` form.

### 3. Durable execution with Temporal (optional)

The crawl above runs in-process. To run it as a durable, schedulable workflow,
start a [Temporal](https://docs.temporal.io/cli) dev server and the worker:

```bash
temporal server start-dev                 # Temporal UI at http://localhost:8233
uv run jill worker                         # in another terminal
JILL_TENANT_ID=1 uv run jill source 1 --no-local   # enqueue the durable workflow
```

---

## Testing

```bash
docker compose -f apps/api/docker-compose.yml up -d db   # Django tests need Postgres

uv run pytest                       # Django / API tests (from the repo root)
cd apps/worker && uv run pytest     # worker tests (downloads a Temporal test server once)
```

---

## Configuration

All optional — defaults cover local development. Override via environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `JILL_MODE` | `mock` | `live` engages real Brightdata / Claude / delivery |
| `BRIGHTDATA_API_KEY` | — | required in live mode for LinkedIn data |
| `ANTHROPIC_API_KEY` | — | required in live mode for Claude scoring/drafting |
| `SERVICE_TOKEN` | `dev-service-token-change-me` | shared secret between the worker and the API |
| `DJANGO_SECRET_KEY` | dev default | set a real value in production |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` | `zenapi` / `zen` / `zen` / `localhost` / `5432` | database connection |
| `TEMPORAL_ADDRESS` | `localhost:7233` | Temporal server address |

Copy `.env.example` to `.env` and fill in any secrets — it is loaded
automatically. Switching to live mode is just `JILL_MODE=live` plus the relevant
keys; no code change.

---

## Project structure

```
apps/api/         Django application: settings, URL routing, service-token auth
apps/worker/      The "jill" package: Brightdata client, Claude scorer/drafter,
                  Temporal workflow + activities, pipeline stages, and the CLI
packages/agent/   The "sourcing" Django app: models, REST API, dashboard, spend
packages/mt/      Multi-tenancy + row-level-security library (shared, do not modify)
docs/sourcing/    Product requirements, plan, constraints, test plan, CLI reference
```

The repository is a `uv` workspace (multiple Python packages managed together).

---

## How it works (one run)

1. A recruiter creates a **role** with an ideal-candidate profile and presses
   "source"; the API records a run and seeds the target companies.
2. The worker scans each company, keeps the **recent joiners**, enriches their
   profiles, and **scores** them against the rubric.
3. Each *fit* lead **expands** the search — its previous employers become new
   companies to monitor, its network becomes new candidates — and gets a
   **personalized draft** (LinkedIn + email).
4. The crawl repeats, bounded by depth and budget, de-duplicating as it goes.
5. The recruiter reviews ranked leads on the dashboard and **approves** outreach;
   only then is anything sent.

---

## License

[MIT](LICENSE)
