# CLI — `jill`

The operator surface for the sourcing agent. It drives the same Temporal workflows
and web-py API the scheduler uses, so the CLI is both the dev/demo tool and the
manual escape hatch. Defaults to **mock mode** (no network, no live LLM/Brightdata).

Implemented as a `click`/`typer` app in the worker package:
`uv run jill <command>` (or `python -m jill`).

## Global options / env

- `--tenant <slug>` (or `JILL_TENANT`) — tenant to act as. Required for writes.
- `--mode mock|live` (default `mock`, or `JILL_MODE`) — toggles Brightdata + LLM +
  outreach between fixtures/stubs and real APIs.
- `--json` — machine-readable output.
- Connection via env: `WEBPY_BASE_URL`, `SERVICE_TOKEN`, `TEMPORAL_ADDRESS`,
  `BRIGHTDATA_API_KEY`, `ANTHROPIC_API_KEY` (only needed in `--mode live`).

## Commands

### Setup / inspection
```
jill health                       # checks web-py, Temporal, and (if live) API keys
jill tenant create <slug> <name>  # create a tenant + service token (dev convenience)
```

### Roles & targets
```
jill role create --title "Founding Eng" --icp ./icp.json
jill role list
jill role show <role_id>          # ICP + target companies + run history
jill target add <role_id> --company "Vapi" --url <linkedin_company_url>
```

### Sourcing (drives Temporal)
```
jill source --role <role_id> [--company "Vapi"]   # start MonitorCompanyWorkflow
                                                  #   monitor→enrich→score→expand→draft
jill run status <run_id>          # workflow state + stage counts
jill run watch <run_id>           # stream progress until terminal
```

### Single-stage helpers (each runs one activity directly — for dev/debug)
```
jill enrich <linkedin_url>                 # scrape + store one profile
jill score --role <role_id> --candidate <id>   # run fit scoring, print scorecard
jill expand --candidate <id>               # show prev-employer + network lead sources
```

### Leads & outreach
```
jill leads --role <role_id> [--verdict fit|drop] [--min-score 70]
                                  # ranked leads with score, reasons, provenance chain
jill lead show <candidate_id>     # profile + scorecard + full LeadSource trail
jill outreach list --role <role_id> [--status draft|approved|sent]
jill outreach show <draft_id>
jill outreach approve <draft_id>  # draft → approved → enqueues send
jill outreach reject <draft_id> --reason "..."
```

### Scheduling
```
jill schedule create --role <role_id> --cron "0 9 * * *"   # daily monitor (Temporal Schedule)
jill schedule list
jill schedule delete <schedule_id>
```

## Example session (the demo path)

```bash
# 0. infra: docker compose up -d db redis  +  temporal server start-dev
jill health
jill tenant create acme "Acme Recruiting"

# 1. define the role + ICP, seed a company
export JILL_TENANT=acme
jill role create --title "Voice AI Eng" --icp ./examples/voice_ai_icp.json   # -> role_1
jill target add role_1 --company "Vapi" --url https://linkedin.com/company/vapi

# 2. source (mock mode: deterministic, no network)
jill source --role role_1                  # -> run_42
jill run watch run_42

# 3. inspect warm leads + provenance, then approve outreach
jill leads --role role_1 --verdict fit --min-score 70
jill lead show cand_7                       # shows: Vapi recent-joiner -> ex-employer Acme -> ...
jill outreach list --role role_1 --status draft
jill outreach approve draft_3               # only now can it send (mock logs the send)
```

## Exit codes

`0` success · `2` bad args / missing tenant · `3` upstream unavailable (web-py /
Temporal down) · `4` budget/limit reached (partial result, not a crash) · `5`
live mode requested without required keys.

## Notes

- Every write command is **idempotent** — re-running `jill source` on the same
  fixtures does not duplicate leads.
- `--mode live` is gated: the CLI refuses (exit 5) if Brightdata/Anthropic keys are
  absent rather than silently degrading.
- The CLI is a thin client over the Temporal client + web-py service API — it adds no
  business logic of its own, so anything it does is reproducible by the scheduler.
