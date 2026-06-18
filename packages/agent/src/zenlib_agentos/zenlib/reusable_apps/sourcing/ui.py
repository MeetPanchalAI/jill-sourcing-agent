"""Minimal server-rendered dashboard for the sourcing agent (bonus UX).

Roles → ranked leads with provenance → approve/reject outreach. Read-only Django
function views (no DRF), scoped to a tenant resolved from ``?tenant=<id>``. Because
there's no login flow here, the view sets ``context.current_tenant`` + the RLS GUC
itself — the same tenant scoping the middleware does for API calls, applied
server-side so the service token never reaches the browser.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.db import connection
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from zenlib.reusable_apps.multitenant import context
from zenlib.reusable_apps.multitenant.models import Tenant

from .models import (
    Candidate,
    LinkedInAccount,
    OutreachDraft,
    Role,
    SourcingRun,
    TargetCompany,
)
from .usage import role_cost


def _latest_run(role: Role) -> SourcingRun | None:
    runs = sorted(role.runs.all(), key=lambda r: r.created_at, reverse=True)
    return runs[0] if runs else None


def _role_card(role: Role) -> dict:
    """Summary shown on the roles list — ICP at a glance + the latest run's tally."""
    icp = role.icp or {}
    return {
        "role": role,
        "latest": _latest_run(role),
        "skills": icp.get("must_have_skills", []),
        "companies": [c.get("name") for c in icp.get("target_companies", [])
                      if c.get("name")],
        "rubric": icp.get("rubric", []),
    }

# A sensible default rubric so a role created from the portal scores richly
# out of the box. The recruiter can refine criteria/weights later via the API.
DEFAULT_RUBRIC = [
    {"name": "Ex-founder", "type": "founder", "weight": 2},
    {"name": "Pedigree", "type": "pedigree", "weight": 2},
    {"name": "Voice/AI domain", "type": "domain", "weight": 2,
     "keywords": ["voice", "audio", "speech", "realtime", "telephony", "ai"]},
    {"name": "Tenure 2-6y", "type": "tenure", "weight": 1,
     "min_years": 2, "max_years": 6},
    {"name": "0-to-1 builder", "type": "open", "weight": 1,
     "description": "early-stage / first-engineer experience"},
]


def _parse_seeds(raw: str) -> list[dict]:
    """Parse the seed box into ``[{name, linkedin_url?}]`` entries.

    Accepts several seeds at once (comma- or newline-separated) — profile-URL
    seeding is the primary path, so a recruiter can paste a list of engineers.
    A LinkedIn URL is kept verbatim (and labelled by its slug for the chip); a
    bare string is treated as a company name."""
    seeds: list[dict] = []
    for item in re.split(r"[\n,]+", raw or ""):
        s = item.strip()
        if not s:
            continue
        if "linkedin.com/" in s:
            slug = s.rstrip("/").split("/")[-1].split("?")[0]
            seeds.append({"name": slug or s, "linkedin_url": s})
        else:
            seeds.append({"name": s})
    return seeds


def _approve_and_send(draft: OutreachDraft) -> None:
    """Approve a draft and, for a LinkedIn invite, send it through the connected
    account immediately (mock delivery), respecting the daily cap. Email stays
    approved and is sent via the email provider separately."""
    draft.approve(by="ui")
    if draft.channel == OutreachDraft.Channel.LINKEDIN:
        acct = LinkedInAccount.objects.first()
        if acct and acct.can_invite():
            draft.mark_sent()
            acct.record_invite()


def _resolve_tenant(request) -> Tenant | None:
    tid = request.GET.get("tenant") or request.POST.get("tenant")
    if tid:
        try:
            return Tenant.objects.get(id=int(tid), is_active=True)
        except (Tenant.DoesNotExist, ValueError):
            return None
    return Tenant.objects.filter(is_active=True).order_by("id").first()


def _activate(tenant: Tenant) -> None:
    """Scope this request to ``tenant`` — ContextVar (manager filter) + RLS GUC.

    ``ATOMIC_REQUESTS`` wraps the view in a transaction, so the ``SET LOCAL`` sticks
    for every query the view runs."""
    context.current_tenant.set(tenant)
    with connection.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.current_tenant_id', %s, true)", [str(tenant.id)]
        )


def roles_index(request):
    tenant = _resolve_tenant(request)
    if tenant is None:
        return render(request, "sourcing/roles.html",
                      {"tenant": None, "roles": [], "tenants": []})
    _activate(tenant)
    roles = Role.objects.prefetch_related("runs").all()
    return render(request, "sourcing/roles.html", {
        "tenant": tenant,
        "tenants": Tenant.objects.filter(is_active=True),
        "role_cards": [_role_card(r) for r in roles],
        "nav": "roles",
    })


def outreach_queue(request):
    """Cross-role outreach queue — every candidate Jill has drafted, grouped by
    status, so the recruiter's whole approval queue lives in one place."""
    tenant = _resolve_tenant(request)
    if tenant is None:
        return render(request, "sourcing/outreach.html",
                      {"tenant": None, "drafts": [], "stat_cards": [],
                       "nav": "outreach"})
    _activate(tenant)
    order = {"draft": 0, "approved": 1, "sent": 2, "rejected": 3}
    drafts = sorted(
        OutreachDraft.objects.select_related("candidate", "role"),
        key=lambda d: (order.get(d.status, 9), -d.id),
    )
    counts = {status: 0 for status in order}
    for draft in drafts:
        counts[draft.status] = counts.get(draft.status, 0) + 1
    stat_cards = [
        ("Awaiting approval", counts["draft"], "accent"),
        ("Approved", counts["approved"], ""),
        ("Sent", counts["sent"], "good"),
        ("Rejected", counts["rejected"], ""),
    ]
    return render(request, "sourcing/outreach.html", {
        "tenant": tenant,
        "tenants": Tenant.objects.filter(is_active=True),
        "drafts": drafts, "stat_cards": stat_cards, "nav": "outreach",
    })


def create_role(request):
    """Create a role from the portal: title + seed company + must-have skills.

    Must-have skills become weighted ``skill`` criteria, prepended to a sensible
    default rubric, so the new role scores leads richly without any API call."""
    tenant = _resolve_tenant(request)
    if tenant is None:
        return redirect("/ui/sourcing/")
    _activate(tenant)
    title = (request.POST.get("title") or "Untitled role").strip()
    companies = _parse_seeds(request.POST.get("company") or "")
    skills = [s.strip() for s in (request.POST.get("skills") or "").split(",")
              if s.strip()]
    skill_criteria = [
        {"name": s, "type": "skill", "skill": s, "weight": 2} for s in skills
    ]
    role = Role.objects.create(
        title=title,
        status=Role.Status.SOURCING,
        icp={
            "target_companies": companies,
            "must_have_skills": skills,
            "rubric": skill_criteria + DEFAULT_RUBRIC,
        },
    )
    return redirect(f"/ui/sourcing/roles/{role.id}/?tenant={tenant.id}")


def delete_candidate(request, role_id: int, candidate_id: int):
    """Remove a candidate from the role's list (hard delete).

    Cascades automatically to their score, drafts, provenance edges and
    enrichment (all FK ``on_delete=CASCADE``), so the lead and its outreach
    disappear from the board together."""
    tenant = _resolve_tenant(request)
    if tenant is None:
        return redirect("/ui/sourcing/")
    _activate(tenant)
    role = get_object_or_404(Role, id=role_id)
    cand = Candidate.objects.filter(id=candidate_id).first()
    if cand is not None:
        # TRUE hard delete (the base model's .delete() only soft-deletes, which
        # would leave the row occupying the (tenant, linkedin_url) unique key and
        # break re-discovery on the next run). hard_delete still cascades to the
        # candidate's score, drafts, edges and enrichment.
        cand.hard_delete()
    # AJAX delete (from the list) updates the DOM in place — no reload, no scroll
    # jump. Plain form posts (no JS) still get the redirect.
    if request.headers.get("x-requested-with") == "fetch":
        return HttpResponse(status=204)
    return redirect(f"/ui/sourcing/roles/{role.id}/?tenant={tenant.id}")


def start_sourcing(request, role_id: int):
    """Run the sourcing pipeline in-process and return to the role with its leads.

    Synchronous: the mock crawl finishes in under a second, so results are ready
    on redirect. The durable production path is the Temporal workflow."""
    tenant = _resolve_tenant(request)
    if tenant is None:
        return redirect("/ui/sourcing/")
    _activate(tenant)
    role = get_object_or_404(Role, id=role_id)

    # Add any company seeds typed inline before sourcing (deduped on URL/name so
    # re-runs don't pile up duplicates).
    new_companies = _parse_seeds(request.POST.get("company") or "")
    if new_companies:
        icp = role.icp or {}
        seeds = icp.get("target_companies", [])
        seen = {
            (c.get("linkedin_url") or c.get("name")) if isinstance(c, dict) else c
            for c in seeds
        }
        for s in new_companies:
            key = s.get("linkedin_url") or s.get("name")
            if key not in seen:
                seeds.append(s)
                seen.add(key)
        icp["target_companies"] = seeds
        role.icp = icp
        role.save(update_fields=["icp"])

    from .agent_runner import run_sourcing_inprocess

    run_sourcing_inprocess(role)
    return redirect(f"/ui/sourcing/roles/{role.id}/?tenant={tenant.id}")


def role_detail(request, role_id: int):
    tenant = _resolve_tenant(request)
    if tenant is None:
        return redirect("/ui/sourcing/")
    _activate(tenant)
    role = get_object_or_404(Role, id=role_id)

    # In live mode, hide candidates produced by the mock (fixture) scorer so the
    # board reflects only real LinkedIn + Claude results. The scorer stamps each
    # Score with its model id ("mock:rules" for the deterministic mock), which is
    # a clean discriminator without tagging every run.
    from jill.config import get_settings

    live_mode = get_settings().is_live

    cands = (
        Candidate.objects.filter(scores__role=role)
        .prefetch_related("scores", "inbound_edges__from_company")
        .distinct()
    )
    leads = []
    hidden_mock = 0
    for c in cands:
        score = next((s for s in c.scores.all() if s.role_id == role.id), None)
        if score is None:
            continue
        if live_mode and (score.model or "").startswith("mock"):
            hidden_mock += 1
            continue
        provenance = [
            {
                "kind": e.kind,
                "depth": e.depth,
                "from": (e.from_company.name if e.from_company
                         else f"candidate {e.from_candidate_id}"),
                "method": e.method,
            }
            # Only edges for *this* role — a candidate sourced for several roles
            # has one edge per role, which would otherwise look like duplicates.
            for e in c.inbound_edges.all() if e.role_id == role.id
        ]
        leads.append({"candidate": c, "score": score, "provenance": provenance})
    leads.sort(key=lambda x: x["score"].score, reverse=True)

    drafts = OutreachDraft.objects.filter(role=role).select_related("candidate", "role")
    if live_mode:
        # Keep the drafts board consistent with the hidden mock leads above.
        drafts = drafts.filter(candidate_id__in={lead["candidate"].id for lead in leads})
    runs = list(SourcingRun.objects.filter(role=role).order_by("-created_at")[:5])
    # Monitoring is a *live projection of the list*: the companies the current
    # candidates work at. Delete every candidate from a company and it drops off
    # Monitoring; add one and it reappears — always in sync with the leads.
    company_counts: dict[str, int] = {}
    for lead in leads:
        name = (lead["candidate"].current_company or "").strip()
        if name:
            company_counts[name] = company_counts.get(name, 0) + 1
    monitoring = [
        {"name": n, "count": c}
        for n, c in sorted(company_counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    ]
    icp = role.icp or {}
    # Rubric grouped into weight tiers, so the brief shows "counts double" vs
    # "standard" as section headers instead of stamping a ×N badge on every chip.
    rubric = sorted(icp.get("rubric", []), key=lambda c: -c.get("weight", 1))
    # Auto-refresh the page while a run is still in flight so status/counters
    # update without a manual reload.
    active = any(r.status in ("pending", "running") for r in runs)
    fit = sum(1 for lead in leads if lead["score"].verdict == "fit")
    summary = [
        ("Companies", len(monitoring), ""),
        ("Leads", len(leads), ""),
        ("Fit", fit, "good"),
        ("Drafts", drafts.count(), "accent"),
    ]
    return render(request, "sourcing/role_detail.html", {
        "tenant": tenant, "role": role, "leads": leads, "drafts": drafts,
        "runs": runs, "auto_refresh": active, "nav": "roles",
        "monitoring": monitoring, "skills": icp.get("must_have_skills", []),
        "rubric_key": [c for c in rubric if c.get("weight", 1) >= 2],
        "rubric_std": [c for c in rubric if c.get("weight", 1) < 2],
        "summary": summary,
        "seeds": [c.get("name") if isinstance(c, dict) else c
                  for c in icp.get("target_companies", [])
                  if (c.get("name") if isinstance(c, dict) else c)],
        "linkedin": LinkedInAccount.objects.first(),
        "cost": role_cost(role),
    })


def outreach_action(request, draft_id: int, action: str):
    tenant = _resolve_tenant(request)
    if tenant is None:
        return redirect("/ui/sourcing/")
    _activate(tenant)
    draft = get_object_or_404(OutreachDraft, id=draft_id)
    try:
        if action == "edit":
            # Review drawer: save the recruiter's wording, then act on it in the
            # same submit (intent) so edits are never lost. Drafts only.
            if draft.status == OutreachDraft.Status.DRAFT:
                body = request.POST.get("body", "").strip()
                draft.subject = request.POST.get("subject", draft.subject).strip()
                if body:
                    draft.body = body
                draft.save()
                intent = request.POST.get("intent", "save")
                if intent == "approve":
                    _approve_and_send(draft)
                elif intent == "reject":
                    draft.reject(reason=request.POST.get("reason", ""))
        elif action == "approve":
            _approve_and_send(draft)
        elif action == "reject":
            draft.reject(reason=request.POST.get("reason", ""))
    except ValidationError:
        pass  # illegal transition — ignore, the list reflects current state
    # Return to wherever the action was taken (the role page or the pipeline).
    nxt = request.POST.get("next")
    if nxt:
        return redirect(f"{nxt}{'&' if '?' in nxt else '?'}tenant={tenant.id}")
    return redirect(f"/ui/sourcing/roles/{draft.role_id}/?tenant={tenant.id}")


def linkedin_connect(request):
    """Connect the recruiter's LinkedIn account (Gojiberry-style session paste)."""
    tenant = _resolve_tenant(request)
    if tenant is None:
        return redirect("/ui/sourcing/")
    _activate(tenant)
    acct, _ = LinkedInAccount.objects.get_or_create()
    acct.connect(
        account_name=request.POST.get("account_name", "My LinkedIn"),
        session_cookie=request.POST.get("session_cookie", "li_at_demo_session"),
        by="ui",
    )
    nxt = request.POST.get("next") or "/ui/sourcing/"
    return redirect(f"{nxt}{'&' if '?' in nxt else '?'}tenant={tenant.id}")
