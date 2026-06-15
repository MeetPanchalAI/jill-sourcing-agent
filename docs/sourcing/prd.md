# PRD — Jill Sourcing Agent

> The "Jill" half of Jack & Jill: an AI recruiter that **finds warm candidates**,
> enriches and filters them against a role's ICP, expands the search through each
> lead's network, and queues consented outreach. This document covers **sourcing
> only** — not the Jack↔Jill negotiation, candidate prep, or the job-seeker side.

## 1. Problem

A recruiter (tenant) wants high-signal candidates for an open role without
manually trawling LinkedIn. Cold keyword search is noisy. The signal we exploit:

- **Recent joiners at relevant companies** are warm — they just made a move, their
  skills are validated by a company we respect, and their *former* colleagues and
  *previous* employers are concentrated pools of similar talent.

Jill automates: monitor target companies → catch recent joiners → enrich →
score against the role's ICP → **expand** through each lead's prior employer and
network → draft personalized invites for human approval.

## 2. Goals / Non-goals

**Goals**
- G1. Given a **Role/ICP**, produce a ranked, de-duplicated list of **warm leads**
  with evidence (why they fit) and a provenance trail (how we found them).
- G2. **Recent-joiner detection** from a monitored company's employee list.
- G3. **Lead-source fan-out**: lead → lead's previous employer (new monitor target)
  → lead's network (new candidates), bounded by depth and budget.
- G4. **LLM fit scoring** against the ICP that drops irrelevant roles with a reason.
- G5. **Draft-and-approve outreach**: personalized LinkedIn + email invites staged
  for a human to approve before anything sends.
- G6. **Durable + scheduled** execution (Temporal): a monitor runs on a cron,
  survives restarts, retries scrapes, and is idempotent.
- G7. **Strict tenant isolation** — every row scoped by Postgres RLS; no cross-tenant
  leakage.

**Non-goals (this take-home)**
- Jack side (job-seeker agent), Jack↔Jill negotiation, interview scheduling.
- Real-time LinkedIn messaging UX, ATS integrations, billing.
- Scraping anything via a method other than **Brightdata**.
- Auto-sending outreach (we draft + require human approval).

## 3. Personas

- **Recruiter / Hiring Manager (tenant user)** — defines roles, reviews leads,
  approves outreach. Authenticates via Knox.
- **Jill (the agent)** — Claude-driven. Decides who to monitor, scores fit, drafts
  outreach. Runs inside the worker, never holds a tenant session directly.
- **Worker (service account)** — the Temporal worker writing results back to web-py
  over `X-Service-Token` + `X-Tenant-Id`. This is how all agent output is persisted.

## 4. System shape (3 layers)

```
┌────────────────────────────────────────────────────────────────────┐
│ web-py (Django + DRF + Postgres + RLS)  ── system of record         │
│   sourcing app: Role, TargetCompany, Candidate, LeadSource,         │
│   EnrichmentProfile, ScoreCard, OutreachDraft, ProcessedEvent       │
│   Service-token API  ◀── worker writes here (X-Tenant-Id ⇒ RLS)     │
└───────────────▲────────────────────────────────────────────────────┘
                │ service-token REST
┌───────────────┴────────────────────────────────────────────────────┐
│ Temporal worker  ── durable orchestration                           │
│   MonitorCompanyWorkflow (scheduled)                                │
│     → DetectRecentJoiners → EnrichLead → ScoreFit                   │
│     → ExpandNetwork (prev employer + connections) → DraftOutreach   │
│   Activities: Brightdata client, Jill agent calls, web-py writes    │
└───────────────▲────────────────────────────────────────────────────┘
                │ tool calls
┌───────────────┴────────────────────────────────────────────────────┐
│ Jill agent (Claude Agent SDK)  ── the reasoning brain               │
│   tools: choose_targets, score_fit, dedupe, draft_outreach          │
└────────────────────────────────────────────────────────────────────┘
```

Brightdata and outreach delivery sit behind **interfaces with a `mock` and a `live`
implementation**. Tests and local dev run `mock` (recorded fixtures, no network);
`live` activates only when API keys are present. This keeps the whole pipeline
runnable and deterministic without scraping LinkedIn or messaging real people.

## 5. Domain model (web-py `sourcing` app)

All inherit `ActivityTenantBaseModel` (tenant FK + soft-delete + RLS, auto-applied).

| Model | Purpose | Key fields |
|---|---|---|
| `Role` | The opening + ICP Jill sources for | `title`, `icp` (JSON: must/nice skills, seniority, locations, target_companies[]), `status` |
| `TargetCompany` | A company we monitor for joiners | `name`, `linkedin_url`, `source` (seed \| prev_employer), `last_scraped_at`, `role` FK |
| `Candidate` | A person we discovered | `linkedin_url` (unique per tenant), `full_name`, `headline`, `current_company`, `current_title`, `started_current_role_at` |
| `LeadSource` | Provenance edge: how a candidate was found | `candidate` FK, `kind` (recent_joiner \| prev_employer \| network), `origin_company`/`origin_candidate`, `depth` |
| `EnrichmentProfile` | Full scraped profile snapshot | `candidate` FK, `raw` (JSON), `experiences`, `skills`, `fetched_at` |
| `ScoreCard` | LLM fit verdict vs a Role | `candidate` FK, `role` FK, `score` (0–100), `verdict` (fit \| drop), `reasons`, `dropped_reason` |
| `OutreachDraft` | Staged invite awaiting approval | `candidate` FK, `role` FK, `channel` (linkedin \| email), `body`, `status` (draft → approved → sent \| rejected), `approved_by`, `sent_at` |
| `ProcessedEvent` | Idempotency ledger | `idempotency_key` (unique per tenant), `kind`, `ref` |

`Candidate` uniqueness `(tenant, linkedin_url)` is the dedupe anchor across all
fan-out paths.

## 6. Core flow — "lead sources" fan-out

1. **Seed**: recruiter creates a `Role` with `icp.target_companies = ["Vapi", …]`.
   Each becomes a `TargetCompany(source=seed)`.
2. **Monitor** (scheduled `MonitorCompanyWorkflow`): Brightdata returns the company's
   employees. **DetectRecentJoiners** keeps those whose `started_current_role_at`
   is within `RECENT_JOINER_WINDOW_DAYS` (default 90). Each becomes a `Candidate`
   with a `LeadSource(kind=recent_joiner, origin_company=…)`.
3. **Enrich**: `EnrichLead` pulls the full profile via Brightdata → `EnrichmentProfile`.
4. **Score**: Jill (`score_fit`) compares the profile to the ICP → `ScoreCard`.
   `verdict=drop` with a `dropped_reason` removes irrelevant roles from the funnel.
5. **Expand** (`ExpandNetwork`, only for `verdict=fit`, bounded by `MAX_EXPANSION_DEPTH`):
   - **Previous employer** → new `TargetCompany(source=prev_employer)` to monitor.
   - **Network/connections** → new `Candidate`s with `LeadSource(kind=network,
     origin_candidate=lead)`.
6. **Draft**: for fit leads, Jill writes a personalized `OutreachDraft` per channel.
7. **Approve & send**: recruiter approves in the UI/CLI; only then does the outreach
   activity deliver (mock by default).

Every fan-out edge is recorded in `LeadSource`, so a lead's full provenance —
*"found via Vapi recent-joiner → their ex-employer Acme → Acme joiner"* — is
queryable.

## 7. API surface (web-py, service-token unless noted)

- `POST /api/v1/sourcing/roles/` — create role + ICP (Knox, recruiter).
- `GET  /api/v1/sourcing/roles/{id}/leads/` — ranked leads with scorecards + provenance.
- `POST /api/v1/sourcing/candidates/` — upsert candidate (service, idempotent).
- `POST /api/v1/sourcing/candidates/{id}/enrichment/` — attach profile (service).
- `POST /api/v1/sourcing/scorecards/` — attach score (service).
- `POST /api/v1/sourcing/outreach/` — create draft (service).
- `POST /api/v1/sourcing/outreach/{id}/approve/` — approve → enqueue send (Knox, recruiter).
- `GET  /api/v1/sourcing/runs/{id}/` — workflow status / counts.

## 8. Success metrics (demoable)

- Given a seed company, the pipeline yields ≥ N de-duplicated **fit** leads with
  scorecards and provenance, **end-to-end in mock mode, deterministically**.
- Fan-out demonstrably reaches depth ≥ 2 (seed → prev employer → its joiners).
- Cross-tenant RLS test passes (a second tenant sees none of tenant A's leads).
- A scheduled monitor re-run is idempotent (no duplicate candidates/events).
- Outreach cannot send without an explicit approval transition.

## 9. Open questions (track in plan.md decisions log)

- Brightdata dataset/endpoint choice for company employees vs profile vs network,
  and which fields are reliably present.
- "Network" reachability via Brightdata — connections aren't always exposed; we may
  approximate network via shared-company / shared-school cohorts.
- Rate-limit / cost budget per monitor run.
