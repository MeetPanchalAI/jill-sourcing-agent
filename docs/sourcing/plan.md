# Plan — Jill Sourcing Agent

Execution plan, milestones, and decisions log. Built to be driven by `/ralph-loop`
once these docs are signed off: each milestone is a vertical, testable slice with a
clear "done" gate.

## Decisions (locked with stakeholder)

| # | Decision | Choice |
|---|---|---|
| D1 | Build depth | **Full pipeline, mockable I/O** — every stage end-to-end; Brightdata/LLM/outreach behind mock+live interfaces. |
| D2 | Agent framework | **Claude Agent SDK** — Jill as a Claude tool-use loop (platform-native MCP/SDK path). |
| D3 | Topology | **Separate Temporal worker** writing to web-py over the `X-Service-Token` API; RLS is the isolation boundary. |
| D4 | Outreach | **Draft + human approval** — no auto-send. |
| D5 | LLM provider | **Claude (Anthropic)**, latest models; tiered (haiku triage / sonnet|opus final). |

## Repo layout (additions)

```
web-py/
├── apps/api/                     # web-py (existing) — add `sourcing` app routes
├── packages/agent/.../sourcing/  # NEW Django app: models, serializers, views, services
│   ├── models/   roles, candidates, lead_sources, enrichment, scorecards, outreach, events
│   ├── views/    DRF viewsets (Knox + service-token)
│   └── migrations/
├── apps/worker/                  # NEW uv workspace member — Temporal worker + Jill + CLI
│   ├── jill/agent/   Claude Agent SDK tools (choose_targets, score_fit, draft_outreach)
│   ├── jill/brightdata/   client iface + mock(fixtures)+live
│   ├── jill/outreach/     deliver iface + mock+live
│   ├── jill/webpy/        service-token API client
│   ├── jill/workflows/    MonitorCompany, EnrichLead, ExpandNetwork (+ activities)
│   ├── jill/cli.py        the `jill` CLI
│   └── tests/
└── docs/sourcing/                # these docs
```

## Milestones

Each milestone: code + tests green + `ruff`/`bandit` clean before moving on.

### M0 — Bootstrap (infra runnable)
- Add `sourcing` app to `INSTALLED_APPS`; add `apps/worker` workspace member.
- `docker compose up db redis`; Temporal dev server documented.
- `.env.example` with all keys; mock mode needs none.
- **Gate:** `uv sync --all-packages`, `manage.py migrate`, existing tests pass.

### M1 — Domain model + RLS  (PRD §5, tests T1)
- Models inheriting `ActivityTenantBaseModel`: `Role`, `TargetCompany`, `Candidate`,
  `LeadSource`, `EnrichmentProfile`, `ScoreCard`, `OutreachDraft`, `ProcessedEvent`.
- Serializers + viewsets; service-token writes, Knox reads. Register routes.
- `makemigrations sourcing && migrate`.
- **Gate:** T1.1 cross-tenant denial + T1.2–T1.4 pass.

### M2 — Brightdata client (mock first)  (constraints C1–C4, tests T6)
- `BrightdataClient` interface: `company_employees(url)`, `profile(url)`,
  `network(profile)`. Record JSON fixtures (Vapi-like) for mock.
- Retry/backoff + typed errors + PII-safe logging.
- **Gate:** T6.1–T6.3 pass; live impl stubbed behind key check.

### M3 — Recent-joiner detection + candidate ingest  (G2, tests T2, T7)
- `DetectRecentJoiners` pure function (windowed, dedup). Upsert `Candidate` +
  `LeadSource(recent_joiner)` via the web-py client, idempotent on `(tenant, url)`.
- **Gate:** T2.1–T2.3, T7.1–T7.2 pass.

### M4 — Jill agent: fit scoring  (G4, constraints C13–C16, tests T4)
- Claude Agent SDK loop with `score_fit` tool; Pydantic `ScoreCard` schema; tiered
  models from config; stub scorer for tests. Persist `ScoreCard`; drop irrelevant
  with reason.
- **Gate:** T4.1–T4.4 pass with the stub; one live smoke behind a key-gated marker.

### M5 — Lead-source fan-out (expansion)  (G3, constraints C11, tests T3)
- `ExpandNetwork`: fit lead → prev employer (`TargetCompany(prev_employer)`) +
  network candidates (`LeadSource(network)`), bounded by depth/budget.
- **Gate:** T3.1–T3.4 pass; provenance chain reconstructable.

### M6 — Outreach drafts + approval  (G5, constraints C17–C19, tests T5)
- Jill `draft_outreach` tool → `OutreachDraft(status=draft)` per channel; state
  machine; `approve/`/`reject/` endpoints; mock delivery.
- **Gate:** T5.1–T5.4 pass; no path from `draft → sent` without approval.

### M7 — Temporal orchestration  (G6, constraints C9–C11, tests T8)
- Compose stages into `MonitorCompanyWorkflow`; activities wrap scrape/LLM/writes;
  deterministic workflow code; Schedule support.
- **Gate:** T8.1–T8.3 pass in the time-skipping test env; replayer clean.

### M8 — CLI + e2e demo  (cli.md, tests T9)
- `jill` commands per cli.md over the Temporal client + web-py API.
- **Gate:** T9.1 smoke: seed company → ≥ N fit leads, depth ≥ 2, queued drafts, all mock.

### M9 (bonus) — Minimal UX
- Thin web-py-served page: roles → ranked leads w/ provenance → approve outreach.
- **Gate:** can run the demo end-to-end from the browser.

## Sequencing / dependencies

```
M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8 → (M9)
                └ M2 feeds M3; M3 feeds M4; M4 gates M5/M6; M7 composes M3–M6
```
M2, M4, M6 each ship a mock impl first so downstream milestones never block on live
credentials. Live adapters are wired but key-gated throughout.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Brightdata schema/fields differ from assumptions | Isolate behind `BrightdataClient`; fixtures define the contract; only the live impl changes when reality differs. |
| LinkedIn "network" not exposed via Brightdata | Approximate network via shared-company/shared-school cohorts; record as `LeadSource(network)` with method noted (D-log). |
| Temporal nondeterminism creeping into workflows | Lint rule + replayer test (T8.1); all IO in activities. |
| LLM cost/flakiness in CI | Stub scorer is the default in tests; live path is a separate key-gated marker. |
| RLS regressions | T1.1 runs in CI as the non-negotiable gate. |

## Execution mode

Once signed off: run `/ralph-loop` milestone by milestone. Each loop = implement the
slice, make its gate tests pass, lint, commit. Stop at each gate for review.

## Decisions log (append-only — bugs in `mt/`, deviations, discoveries)

- **2026-06-14 — `makemigrations` writes into `packages/mt/`.** With
  `DEFAULT_AUTO_FIELD = BigAutoField`, Django wants to alter `Tenant.id`
  (AutoField → BigAutoField) and generates `multitenant/0002_alter_tenant_id`
  inside the forbidden library (C7). Routed around: deleted the generated
  migration and repointed `sourcing/0001_initial` to depend on
  `multitenant.0001_initial`. The id-type change is cosmetic and irrelevant to
  the sourcing app.
- **2026-06-14 — per-request queryset construction is mandatory (real bug
  caught by tests).** A DRF viewset `queryset = Model.objects.all()` class
  attribute is evaluated once at import; the tenant auto-filtering manager reads
  `context.current_tenant` at construction time, so it froze the filter to
  whichever tenant was active on first import. Every later tenant then saw zero
  rows. Fix: all sourcing viewsets build their queryset in `get_queryset()` so
  the live tenant context applies per request. (RLS still prevents *leakage*;
  this bug caused *starvation*.) Codified as a convention in `views/__init__.py`.
- **2026-06-14 — ruff RUF012 exempted for Django/DRF Meta layers.** The rule
  flags `Meta.constraints`/serializer `fields` lists; they are framework config,
  not shared mutable state. Per-file ignores added in `ruff.toml`.
- **2026-06-14 — Temporal workflow sandbox leaked through the package `__init__`.**
  The sandbox reloads `jill.workflows.workflow`, which imports the package
  `__init__`; that originally re-exported `activities` (httpx/anthropic) →
  `RestrictedWorkflowAccessError`. Fix: keep `workflows/__init__.py` import-light
  (types + workflow only); import `activities`/`runner` from their submodules.
  Convention codified in the `__init__` docstring.
- **2026-06-14 — DRF auto-`UniqueValidator` on a OneToOne blocks upsert.** The
  `Enrichment.candidate` OneToOne made DRF attach a `UniqueValidator`, so the
  second POST 400'd before the view's `update_or_create` ran. Fix: declare the
  field with `validators=[]` (pass the manager, not a pre-evaluated queryset, so
  tenant scoping stays per-request). Constraints that include `tenant` (Score,
  OutreachDraft) are skipped by DRF automatically, so only Enrichment needed it.
- **2026-06-15 — LinkedIn account (Gojiberry-style) + spend visualizer.**
  `LinkedInAccount` (one per tenant, RLS) stores the session cookie (never
  serialized), keeps it alive via `verify()`, and enforces a daily invite cap.
  Approving a LinkedIn outreach draft in the dashboard **sends the invite through
  the connected account in one click** (mock delivery), records it against the
  cap; without an account it stays `approved`. CLI: `jill linkedin connect/status`.
  Spend is estimated from run counters × a config price table (`usage.role_cost`,
  `SOURCING_PRICES`) — Brightdata scrapes + Claude calls + outreach — shown as a
  dashboard Spend card and `jill costs <role>` / `roles/{id}/costs/`. 5 tests.
  (Migration `0005`; the recurring forbidden `mt/0002` regeneration was reversed +
  removed again per C7.)
- **2026-06-15 — structured rubric scoring + Jack & Jill design system.** The
  scorer moved from skill-overlap to a weighted, typed **rubric** (`agent/rubric.py`)
  — founder / pedigree (IIT/NIT/BITS…) / skill / domain / tenure / open — each
  criterion `met|partial|missed` with grounded evidence, plus a crisp `summary`.
  `Score` gained `criteria` + `summary` (migration `0004`). The dashboard was
  rebuilt to the Jack & Jill design language (cream canvas, serif wordmark,
  terracotta accent, left sidebar) and renders per-lead score badges, the
  summary, and status-coloured criteria chips. The HF demo + CLI surface the
  summary too. 7 new rubric tests; legacy skill path retained for back-compat.
- **2026-06-14 — TargetCompany create wasn't idempotent on the real server
  (caught running the durable Temporal path).** The worker calls `create_target`
  on every scan; `TargetCompanyViewSet.create` was plain DRF create, so a re-run
  hit the `(tenant, role, name)` unique constraint → 500 → failed activity →
  failed workflow. `FakeWebPy.create_target` *was* idempotent, so in-process
  tests stayed green and hid the gap — a mock-vs-real divergence. Fix:
  `update_or_create` on `(role, name)`, matching the other writes; regression
  test added (`test_target_create_is_idempotent`). Lesson: the fake must mirror
  the server's idempotency exactly, and the durable path needs at least one
  real-server smoke run.
- **2026-06-14 — shared `evaluate_lead` keeps workflow + local runner in sync.**
  The per-lead judgment (enrich→score→expand→draft) lives in
  `pipeline/evaluate.py`; both the Temporal `evaluate_candidate` activity and the
  in-process `run_sourcing` (powering `jill demo` / `jill source --local`) call
  it, so the durable and dev paths can't diverge.
