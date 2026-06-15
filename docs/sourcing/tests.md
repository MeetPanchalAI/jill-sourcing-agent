# Tests — Jill Sourcing Agent

Test-first where it pays. Each constraint in `constraints.md` maps to at least one
test below. Everything runs in **mock mode** — no network, no live LLM, deterministic.

Run: `cd apps/api && uv run pytest -q` (web-py) and `uv run pytest -q` in the worker
package. Marker `@pytest.mark.django_db` for DB tests; Temporal tests use the
time-skipping test environment.

## 1. Multitenancy / RLS  (gates C5–C8)

- **T1.1 cross-tenant denial** — model after `tests/test_multitenant.py`. Create a
  `Role` + `Candidate` + `OutreachDraft` under tenant A; in tenant B's context the
  querysets return nothing; a direct id fetch 404s. *The headline isolation test.*
- **T1.2 tenant auto-populate** — saving a `Candidate` inside `current_tenant`
  context stamps the tenant; saving with no tenant raises `ValidationError`.
- **T1.3 soft-delete** — `delete()` sets `is_deleted`; default manager hides it;
  provenance rows survive.
- **T1.4 service-token scoping** — a service call with tenant A's `X-Tenant-Id`
  cannot read tenant B rows even with a valid token.

## 2. Recent-joiner detection  (gates G2)

- **T2.1 window boundary** — given an employee list fixture with `started_at` at
  89/90/91 days, exactly those within `RECENT_JOINER_WINDOW_DAYS` are kept.
- **T2.2 missing start date** — employees with no parseable start date are excluded
  (and counted), not crashed on.
- **T2.3 dedupe** — the same person appearing twice in a scrape yields one `Candidate`.

## 3. Lead-source fan-out  (gates G3, C11)

- **T3.1 provenance edges** — a recent-joiner lead produces `LeadSource(kind=recent_joiner)`;
  expansion produces `prev_employer` (a new `TargetCompany`) and `network` edges.
- **T3.2 depth bound** — fan-out stops at `MAX_EXPANSION_DEPTH`; no candidate exceeds it.
- **T3.3 budget bound** — `MAX_LEADS_PER_RUN` / `MAX_SCRAPES_PER_RUN` are honored;
  the run reports "budget reached" rather than overrunning.
- **T3.4 provenance query** — a lead's full chain (seed → prev employer → joiner) is
  reconstructable from `LeadSource`.

## 4. Fit scoring  (gates G4, C13–C16)

- **T4.1 fit vs drop** — stub scorer: a profile matching ICP must-haves → `verdict=fit`
  with a numeric score and reasons; an off-target profile → `verdict=drop` with a
  `dropped_reason`.
- **T4.2 schema validation** — malformed LLM output retries once; a second failure
  fails the activity and persists nothing.
- **T4.3 grounding** — a `fit` scorecard's reasons reference fields that exist in the
  enrichment profile (no hallucinated evidence).
- **T4.4 ranking** — `GET roles/{id}/leads/` returns fit leads ordered by score desc,
  drops excluded.

## 5. Outreach state machine  (gates G5, C17–C19)

- **T5.1 no auto-send** — a fresh `OutreachDraft` is `status=draft`; the send activity
  refuses anything not `approved`.
- **T5.2 approval transition** — `approve/` moves `draft → approved` and enqueues send;
  `reject/` → `rejected`; illegal transitions (`draft → sent`) raise.
- **T5.3 personalization grounding** — draft body references only profile/provenance
  facts (assert against the fixture's known fields; no invented mutual connections).
- **T5.4 delivery mock** — approved send in mock mode records `sent_at` + a logged
  send, makes no network call.

## 6. Brightdata client  (gates C1–C4)

- **T6.1 mock fixtures** — `mock` client returns recorded company/profile fixtures
  with zero network (assert via a no-network guard / monkeypatched transport).
- **T6.2 retry/backoff** — a simulated 429 triggers bounded retry then surfaces a
  typed error; no infinite loop.
- **T6.3 no PII in logs** — log capture during a scrape contains no raw profile text.

## 7. Idempotency  (gates C8, C10)

- **T7.1 monitor re-run** — running `MonitorCompanyWorkflow` twice on the same
  fixture creates candidates/scorecards/drafts once; `ProcessedEvent` blocks dupes.
- **T7.2 activity replay** — re-invoking an enrich/score/write activity with the same
  `idempotency_key` is a no-op second time.

## 8. Temporal workflows  (gates C9–C11)

- **T8.1 determinism / replay** — workflow histories replay without non-determinism
  errors (Temporal replayer test); no `datetime.now`/IO in workflow code.
- **T8.2 happy path e2e** — in the time-skipping test env with mock activities, a
  seed company runs monitor→enrich→score→expand→draft and yields the expected
  counts of fit leads and drafts.
- **T8.3 activity failure** — a scrape activity that fails N times is retried per
  policy and the workflow degrades gracefully (partial results, run marked).

## 9. End-to-end smoke (CLI)  (gates §8 metrics, C23)

- **T9.1** `jill source --role <id>` in mock mode against the Temporal dev server +
  Postgres yields ≥ N de-duplicated fit leads with scorecards and provenance, depth ≥ 2,
  and queued drafts — asserted via the web-py API. The demo path, run in CI nightly.

## Coverage targets

- Pipeline logic (detection, fan-out, scoring adapters, state machine): high unit
  coverage, fully mocked.
- One real cross-tenant RLS test (T1.1) is the non-negotiable gate, mirroring the
  baseline's reference test.
