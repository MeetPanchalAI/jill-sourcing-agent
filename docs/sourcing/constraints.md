# Constraints — Jill Sourcing Agent

Hard rules the implementation must hold. Violations are bugs, not preferences.

## Scraping

- **C1. Brightdata is the only path to LinkedIn data.** No direct HTTP scraping,
  no headless browser, no third-party scrapers. All LinkedIn reads go through the
  `BrightdataClient` interface.
- **C2. Scraping I/O is mockable.** `BrightdataClient` has a `mock` impl (recorded
  JSON fixtures, zero network) and a `live` impl. `mock` is the default; `live`
  engages only when `BRIGHTDATA_API_KEY` is set. Tests never hit the network.
- **C3. Respect rate limits & cost.** Every scrape is a Temporal activity with a
  bounded retry policy, timeout, and a per-run budget cap (`MAX_SCRAPES_PER_RUN`).
  Back off on Brightdata 429/5xx; never tight-loop.
- **C4. Treat scraped data as PII.** Store only what the pipeline needs. No raw
  profiles in logs. Soft-delete cascades. Honor a per-tenant purge path.

## Multitenancy & data

- **C5. Every domain model inherits `ActivityTenantBaseModel`.** Never add a manual
  `tenant` field or write RLS SQL — the base class + `post_migrate` signal own it.
- **C6. Never bypass RLS.** No `.all_with_deleted()` or raw SQL that reads across
  tenants in request/worker paths. The worker always sets `X-Tenant-Id`; the agent
  never reads the DB directly.
- **C7. Do not modify `packages/mt/`,** the `MIDDLEWARE` order, or the `Tenant` model.
  If the library blocks us, log it in plan.md's decisions log and route around it.
- **C8. Idempotency is mandatory.** Every externally-triggered write carries an
  `idempotency_key` recorded in `ProcessedEvent`. Re-running a monitor must not
  create duplicate candidates, scorecards, or drafts. `Candidate` is unique on
  `(tenant, linkedin_url)`.

## Temporal / orchestration

- **C9. Workflow code is deterministic.** No network, no `datetime.now()`, no
  randomness, no DB access inside workflow functions — all side effects live in
  **activities**. Use Temporal's clock and `workflow.uuid4()`/search attributes.
- **C10. Activities are retryable and idempotent.** Safe to run twice. External
  effects (scrape, LLM, web-py write) are guarded by `ProcessedEvent` or natural keys.
- **C11. Bounded fan-out.** Expansion respects `MAX_EXPANSION_DEPTH` (default 2) and
  `MAX_LEADS_PER_RUN`. No unbounded recursion through the network graph.
- **C12. The worker authenticates as a service account** (`X-Service-Token` +
  `X-Tenant-Id`). It holds no Knox session and cannot act outside its tenant.

## LLM (Jill's brain)

- **C13. Provider is Claude (Anthropic), latest models.** Default the final scoring/
  drafting to a capable model (`claude-opus-4-8` or `claude-sonnet-4-6`); use a
  cheaper tier (`claude-haiku-4-5`) for routing/triage. Model IDs come from config,
  never hardcoded in business logic.
- **C14. Structured, schema-validated output.** Fit scores and outreach drafts are
  parsed into Pydantic schemas; malformed output is retried once, then fails the
  activity (no silent garbage persisted).
- **C15. LLM is mockable/deterministic in tests.** A stub scorer returns fixed
  verdicts so pipeline tests don't depend on a live API or token cost.
- **C16. Grounded scoring only.** A `verdict` must cite evidence from the enrichment
  profile; a `drop` must carry a `dropped_reason`. No fit claim without provenance.

## Outreach & consent

- **C17. No auto-send.** Outreach is created as `status=draft`. Delivery happens only
  after an explicit `approve` transition by a recruiter. The state machine forbids
  `draft → sent`.
- **C18. Delivery is mockable.** Same pattern as Brightdata: `mock` logs the send,
  `live` engages with real keys. Default is `mock`.
- **C19. Personalization must be truthful.** Drafts reference only facts present in
  the candidate's profile/provenance — no fabricated mutual connections or details.

## Engineering hygiene

- **C20. Secrets via env only** (`.env`, never committed). `.env.example` documents
  every key. Mock mode requires zero secrets.
- **C21. Config is centralized** (a `settings`/`config` object), no magic numbers in
  flow code — windows, depths, budgets, model IDs all live in config.
- **C22. Lint + tests green.** `ruff` clean, `bandit` clean on non-test code, and the
  test suite (incl. the cross-tenant RLS test) passes before any stage is "done."
- **C23. Reproducible runs.** A documented `cli` + `docker compose up db redis` + the
  Temporal dev server is enough to run the full pipeline in mock mode on a clean
  machine.
