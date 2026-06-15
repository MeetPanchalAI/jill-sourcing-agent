"""Minimal server-rendered dashboard for the sourcing agent (bonus UX).

Roles → ranked leads with provenance → approve/reject outreach. Read-only Django
function views (no DRF), scoped to a tenant resolved from ``?tenant=<id>``. Because
there's no login flow here, the view sets ``context.current_tenant`` + the RLS GUC
itself — the same tenant scoping the middleware does for API calls, applied
server-side so the service token never reaches the browser.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import connection
from django.shortcuts import get_object_or_404, redirect, render
from zenlib.reusable_apps.multitenant import context
from zenlib.reusable_apps.multitenant.models import Tenant

from .models import (
    Candidate,
    LinkedInAccount,
    OutreachDraft,
    Role,
    SourcingRun,
)
from .usage import role_cost

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
    return render(request, "sourcing/roles.html", {
        "tenant": tenant,
        "tenants": Tenant.objects.filter(is_active=True),
        "roles": Role.objects.all(),
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
    company = (request.POST.get("company") or "").strip()
    skills = [s.strip() for s in (request.POST.get("skills") or "").split(",")
              if s.strip()]
    skill_criteria = [
        {"name": s, "type": "skill", "skill": s, "weight": 2} for s in skills
    ]
    role = Role.objects.create(
        title=title,
        status=Role.Status.SOURCING,
        icp={
            "target_companies": [{"name": company}] if company else [],
            "must_have_skills": skills,
            "rubric": skill_criteria + DEFAULT_RUBRIC,
        },
    )
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

    from .agent_runner import run_sourcing_inprocess

    run_sourcing_inprocess(role)
    return redirect(f"/ui/sourcing/roles/{role.id}/?tenant={tenant.id}")


def role_detail(request, role_id: int):
    tenant = _resolve_tenant(request)
    if tenant is None:
        return redirect("/ui/sourcing/")
    _activate(tenant)
    role = get_object_or_404(Role, id=role_id)

    cands = (
        Candidate.objects.filter(scores__role=role)
        .prefetch_related("scores", "inbound_edges__from_company")
        .distinct()
    )
    leads = []
    for c in cands:
        score = next((s for s in c.scores.all() if s.role_id == role.id), None)
        if score is None:
            continue
        provenance = [
            {
                "kind": e.kind,
                "depth": e.depth,
                "from": (e.from_company.name if e.from_company
                         else f"candidate {e.from_candidate_id}"),
                "method": e.method,
            }
            for e in c.inbound_edges.all()
        ]
        leads.append({"candidate": c, "score": score, "provenance": provenance})
    leads.sort(key=lambda x: x["score"].score, reverse=True)

    drafts = OutreachDraft.objects.filter(role=role).select_related("candidate")
    runs = SourcingRun.objects.filter(role=role).order_by("-created_at")[:5]
    # Auto-refresh the page while a run is still in flight so status/counters
    # update without a manual reload.
    active = any(r.status in ("pending", "running") for r in runs)
    return render(request, "sourcing/role_detail.html", {
        "tenant": tenant, "role": role, "leads": leads, "drafts": drafts,
        "runs": runs, "auto_refresh": active,
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
        if action == "approve":
            draft.approve(by="ui")
            # One-click "take action": for a LinkedIn invite, send it through the
            # connected account immediately (mock delivery), respecting the daily
            # cap. Email stays approved (sent via Instantly separately).
            if draft.channel == OutreachDraft.Channel.LINKEDIN:
                acct = LinkedInAccount.objects.first()
                if acct and acct.can_invite():
                    draft.mark_sent()
                    acct.record_invite()
        elif action == "reject":
            draft.reject(reason=request.POST.get("reason", ""))
    except ValidationError:
        pass  # illegal transition — ignore, the list reflects current state
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
