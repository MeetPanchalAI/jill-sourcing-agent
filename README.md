# Jill — AI Sourcing Agent

Jill is an AI recruiting agent that finds warm candidates. You give it a role and a
target company; Jill watches that company for people who **recently joined**, scores
each against your criteria, follows strong leads to their **previous employers and
former colleagues**, and writes outreach for you to approve. You drive the whole
thing from a web portal.

Runs fully offline with built-in sample data — **no API keys needed** to try it.

---

## What does what

| Piece | What it does |
| --- | --- |
| **Portal** (`/ui/sourcing/`) | The web app you use: create roles, start sourcing, review ranked leads, approve outreach. This is all you need. |
| **Sourcing pipeline** | The agent loop: monitor a company → score its recent joiners → expand through each strong lead's previous employers & network → draft outreach. Bounded by depth and budget. |
| **Rubric scorer** | Scores each candidate 0–100 against weighted criteria (skills, ex-founder, pedigree, domain, tenure) with a per-criterion breakdown. Uses Claude in live mode, deterministic rules in mock mode. |
| **Database** (PostgreSQL) | Stores everything, with **row-level security** so each company's data is fully isolated from the others. |
| **`.env` file** | All configuration in one place. Copy `.env.example` to `.env`; the defaults work as-is. |
| **`jill` CLI** *(optional)* | A command-line mirror of the portal. `uv run jill demo` runs the whole pipeline in memory and prints the results. |
| **Temporal worker** *(optional)* | Runs sourcing as a **durable, resumable** workflow (automatic retries, resume-after-crash) for production. |

---

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — the package manager
- **Docker** — to run PostgreSQL

---

## Setup

```bash
git clone https://github.com/MeetPanchalAI/jill-sourcing-agent.git
cd jill-sourcing-agent

uv sync --all-packages                                   # install everything
cp .env.example .env                                     # config — defaults work as-is
docker compose -f apps/api/docker-compose.yml up -d db   # start PostgreSQL
uv run python apps/api/manage.py migrate                 # create the database tables
uv run python apps/api/manage.py create_tenant "Acme"    # create your company (a "tenant")
uv run python apps/api/manage.py runserver 8000          # start the portal
```

> **Windows:** replace `cp` with `Copy-Item`; every other command is identical.

---

## Use it

Open **http://localhost:8000** — you land on the portal.

1. **New role** — enter a title, a seed company (e.g. `Vapi`), and must-have skills.
2. **Start sourcing** — Jill crawls, scores, and drafts. Ranked leads appear in seconds.
3. **Review** — each lead shows its score, rubric breakdown, and how it was found.
4. **Approve** — outreach is staged as drafts; nothing is sent until you approve it.

That's the whole flow — no command line needed.

---

## Configuration

All settings live in `.env` (copied from `.env.example`). The defaults run everything
**offline in mock mode**, so you can ignore this section to start.

- Real AI scoring/drafting → set `JILL_MODE=live` and `ANTHROPIC_API_KEY`.
- Real LinkedIn data → also set `BRIGHTDATA_API_KEY` (otherwise LinkedIn stays mocked).

Database, ports, and secrets all have working local defaults.

---

## Tests

```bash
docker compose -f apps/api/docker-compose.yml up -d db   # tests need Postgres
uv run pytest                                            # API + portal tests
cd apps/worker && uv run pytest                          # worker / pipeline tests
```

---

## Optional: command line & durable workflows

```bash
uv run jill demo     # run the full monitor → score → expand → draft pipeline in memory
```

To run sourcing as a durable Temporal workflow (production-grade retries and
resume-after-crash) instead of in-process, see [docs/sourcing/](docs/sourcing/).

---

## License

[MIT](LICENSE)
