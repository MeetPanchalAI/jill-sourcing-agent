"""``jill`` — the operator CLI for the sourcing agent.

Thin client over the web-py service API + the Temporal client. Defaults to mock
mode (no network, no live LLM/Brightdata). ``jill demo`` runs the whole pipeline
in-memory with zero dependencies — the fastest way to see Jill work.

Tenant: most commands need a tenant id (``--tenant`` or ``JILL_TENANT_ID``).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import typer

from .config import get_settings

app = typer.Typer(help="Jill sourcing agent", no_args_is_help=True)
role_app = typer.Typer(help="Roles & ICPs", no_args_is_help=True)
outreach_app = typer.Typer(help="Outreach drafts", no_args_is_help=True)
linkedin_app = typer.Typer(help="LinkedIn account", no_args_is_help=True)
app.add_typer(role_app, name="role")
app.add_typer(outreach_app, name="outreach")
app.add_typer(linkedin_app, name="linkedin")


# --- helpers ---------------------------------------------------------------


def _tenant(tenant: int | None) -> int:
    tid = tenant if tenant is not None else os.environ.get("JILL_TENANT_ID")
    if tid is None:
        typer.secho("set --tenant or JILL_TENANT_ID", fg=typer.colors.RED)
        raise typer.Exit(2)
    return int(tid)


def _client(tenant: int | None):
    from .webpy import get_webpy_client

    return get_webpy_client(_tenant(tenant))


def _load_icp(value: str) -> dict:
    """``--icp`` accepts a path to a JSON file or an inline JSON string.

    Uses utf-8-sig so a UTF-8 BOM (which PowerShell's ``Out-File -Encoding utf8``
    prepends) is stripped transparently.
    """
    p = Path(value)
    if p.exists():
        text = p.read_text(encoding="utf-8-sig")
    else:
        text = value.lstrip("﻿")
    return json.loads(text)


def _seed_companies(icp: dict) -> list[str]:
    out = []
    for c in icp.get("target_companies", []):
        name = c.get("name") if isinstance(c, dict) else c
        if name:
            out.append(name)
    return out


# --- commands --------------------------------------------------------------


@app.command()
def demo(
    company: str = typer.Option("Vapi", help="Seed company to source from."),
    role_title: str = typer.Option("Voice AI Engineer"),
):
    """Run the full pipeline in-memory (no servers) and print the ranked leads."""
    from datetime import date

    from .agent.drafting import TemplateDrafter
    from .agent.scoring import RuleScorer
    from .brightdata.mock import MockBrightdataClient
    from .pipeline.run import run_sourcing
    from .webpy.fake import FakeWebPy

    icp = {
        "must_have_skills": ["Python", "Realtime Audio"],
        "rubric": [
            {"name": "Ex-founder", "type": "founder", "weight": 2},
            {"name": "Pedigree", "type": "pedigree",
             "schools": ["IIT", "NIT", "BITS", "Stanford", "MIT", "CMU", "Berkeley"],
             "weight": 2},
            {"name": "Python", "type": "skill", "skill": "Python", "weight": 1},
            {"name": "Voice domain", "type": "domain",
             "keywords": ["voice", "speech", "audio", "realtime"], "weight": 2},
            {"name": "Tenure 2-6y", "type": "tenure", "min_years": 2,
             "max_years": 6, "weight": 1},
            {"name": "0-to-1 builder", "type": "open",
             "description": "Evidence of early-stage / 0-1 building", "weight": 1},
        ],
    }
    client = FakeWebPy()
    result = run_sourcing(
        client, MockBrightdataClient(), RuleScorer(), TemplateDrafter(),
        role_id=1, run_id=1, role_title=role_title, icp=icp,
        seed_companies=[company], as_of=date(2026, 6, 14),
    )
    typer.secho(
        f"\nRun {result.status}: scanned {result.scanned} companies, "
        f"found {result.found} joiners, evaluated {result.evaluated}, "
        f"{result.fit} fit, {result.drafted} drafts.\n",
        fg=typer.colors.GREEN, bold=True,
    )
    # rank fit candidates by score
    fits = sorted(
        ((cid, role), s) for (cid, role), s in client.scores.items()
        if s["verdict"] == "fit"
    )
    fits.sort(key=lambda kv: kv[1]["score"], reverse=True)
    by_id = {c["id"]: c for c in client.candidates.values()}
    typer.secho("Ranked leads", bold=True)
    for (cid, _role), s in fits:
        cand = by_id.get(cid, {})
        edges = [e for e in client.edges.values() if e["to_candidate"] == cid]
        prov = ", ".join(sorted({e["kind"] for e in edges})) or "seed"
        typer.echo(
            f"  [{s['score']:>3}] {cand.get('full_name', cid):<16} "
            f"{cand.get('current_company', ''):<12} via {prov}"
        )
        if s.get("summary"):
            typer.secho(f"        {s['summary']}", fg=typer.colors.BRIGHT_BLACK)
    typer.echo(f"\n{len(client.outreach)} outreach drafts staged for approval.")


@app.command()
def worker():
    """Run the Temporal worker — executes durable sourcing workflows.

    Needs a Temporal server (TEMPORAL_ADDRESS, default localhost:7233) and the
    same SERVICE_TOKEN / WEBPY_BASE_URL the activities use to write to web-py.
    """
    from .workflows.runner import run_worker, temporal_address

    typer.secho(f"jill worker → Temporal at {temporal_address()} "
                f"(task queue: jill-sourcing). Ctrl-C to stop.",
                fg=typer.colors.GREEN)
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        typer.echo("worker stopped")


@app.command()
def health(tenant: int = typer.Option(None)):
    """Check web-py reachability (and report mode)."""
    s = get_settings()
    typer.echo(f"mode: {s.mode}")
    try:
        ok = _client(tenant).health()
        typer.secho(f"web-py: ok ({ok})", fg=typer.colors.GREEN)
    except Exception as exc:
        typer.secho(f"web-py: unreachable ({exc})", fg=typer.colors.RED)
        raise typer.Exit(3) from exc


@app.command()
def source(
    role_id: int,
    tenant: int = typer.Option(None),
    local: bool = typer.Option(True, help="Run in-process (no Temporal server)."),
):
    """Start a sourcing run for a role."""
    client = _client(tenant)
    role = client.get_role(role_id)
    run = client.source_role(role_id)
    run_id = run.data["id"]
    seeds = _seed_companies(role.get("icp", {}))
    typer.echo(f"run {run_id} started for role {role_id} (seeds: {seeds})")

    if local:
        from .agent.drafting import get_drafter
        from .agent.scoring import get_scorer
        from .brightdata import get_client
        from .pipeline.run import run_sourcing

        s = get_settings()
        result = run_sourcing(
            client, get_client(s), get_scorer(s), get_drafter(s),
            role_id=role_id, run_id=run_id, role_title=role["title"],
            icp=role.get("icp", {}), seed_companies=seeds,
            max_depth=s.max_expansion_depth, window_days=s.recent_joiner_window_days,
            max_leads=s.max_leads_per_run,
        )
        typer.secho(f"{result.status}: {result.fit} fit, {result.drafted} drafts",
                    fg=typer.colors.GREEN)
    else:
        from .workflows.runner import connect, start_sourcing_run
        from .workflows.types import SourcingInput

        s = get_settings()

        async def _go():
            tc = await connect()
            return await start_sourcing_run(tc, SourcingInput(
                role_id=role_id, run_id=run_id, tenant_id=_tenant(tenant),
                role_title=role["title"], icp=role.get("icp", {}),
                seed_companies=seeds, max_depth=s.max_expansion_depth,
                window_days=s.recent_joiner_window_days,
                max_leads=s.max_leads_per_run,
            ))

        typer.echo(json.dumps(asyncio.run(_go())))


@app.command()
def leads(
    role_id: int,
    tenant: int = typer.Option(None),
    verdict: str = typer.Option(None),
    min_score: int = typer.Option(None, "--min-score"),
):
    """List ranked leads for a role."""
    params = {}
    if verdict:
        params["verdict"] = verdict
    if min_score is not None:
        params["min_score"] = min_score
    for lead in _client(tenant).leads(role_id, **params):
        prov = ", ".join(p["kind"] for p in lead.get("provenance", [])) or "-"
        typer.echo(
            f"[{lead.get('score')}] {lead.get('full_name')} "
            f"({lead.get('current_company')}) - {lead.get('verdict')} via {prov}"
        )
        if lead.get("summary"):
            typer.secho(f"     {lead['summary']}", fg=typer.colors.BRIGHT_BLACK)


@app.command()
def costs(role_id: int, tenant: int = typer.Option(None)):
    """Estimated spend for a role (Brightdata + Claude + outreach)."""
    c = _client(tenant).costs(role_id)
    typer.secho(f"${c['total_usd']}", fg=typer.colors.GREEN, bold=True)
    typer.echo(
        f"  {c['scrapes']} scrapes ({c['brightdata_cents']}c) - "
        f"{c['llm_calls']} Claude calls ({c['llm_cents']}c) - "
        f"{c['invites_sent']} invites + {c['emails_sent']} emails "
        f"({c['outreach_cents']}c)"
    )


@linkedin_app.command("connect")
def linkedin_connect(
    account_name: str = typer.Option(..., "--name"),
    session_cookie: str = typer.Option("li_at_demo_session", "--cookie",
                                       help="li_at session cookie (mock by default)"),
    tenant: int = typer.Option(None),
):
    """Connect a LinkedIn account so Jill can send invites through it."""
    out = _client(tenant).linkedin_connect(account_name, session_cookie)
    typer.secho(f"connected {out.data['account_name']} - "
                f"{out.data['invites_remaining']} invites/day available",
                fg=typer.colors.GREEN)


@linkedin_app.command("status")
def linkedin_status(tenant: int = typer.Option(None)):
    acct = _client(tenant).linkedin_status().get("account")
    if not acct:
        typer.echo("no LinkedIn account connected")
        return
    typer.echo(f"{acct['account_name']}: {acct['status']} - "
               f"{acct['invites_remaining']}/{acct['daily_invite_limit']} "
               "invites left today")


@role_app.command("create")
def role_create(
    title: str = typer.Option(..., "--title"),
    icp: str = typer.Option("{}", help="JSON file path or inline JSON."),
    tenant: int = typer.Option(None),
):
    """Create a role + ICP."""
    out = _client(tenant).create_role(title=title, icp=_load_icp(icp))
    typer.echo(f"role {out.id} created")


@role_app.command("list")
def role_list(tenant: int = typer.Option(None)):
    data = _client(tenant).list_roles()
    for r in data.get("results", data if isinstance(data, list) else []):
        typer.echo(f"{r['id']}: {r['title']} ({r['status']})")


@outreach_app.command("list")
def outreach_list(
    role_id: int = typer.Option(None, "--role"),
    status: str = typer.Option(None),
    tenant: int = typer.Option(None),
):
    params = {}
    if status:
        params["status"] = status
    data = _client(tenant).list_outreach(**params)
    for d in data.get("results", []):
        if role_id and d.get("role") != role_id:
            continue
        typer.echo(f"{d['id']}: {d['channel']} c{d['candidate']} [{d['status']}]")


@outreach_app.command("approve")
def outreach_approve(draft_id: int, tenant: int = typer.Option(None)):
    out = _client(tenant).approve_outreach(draft_id)
    typer.secho(f"draft {draft_id} -> {out.data['status']}", fg=typer.colors.GREEN)


@outreach_app.command("reject")
def outreach_reject(
    draft_id: int,
    reason: str = typer.Option("", "--reason"),
    tenant: int = typer.Option(None),
):
    out = _client(tenant).reject_outreach(draft_id, reason=reason)
    typer.echo(f"draft {draft_id} -> {out.data['status']}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
