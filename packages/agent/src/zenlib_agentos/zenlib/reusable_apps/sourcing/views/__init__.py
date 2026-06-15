"""DRF viewsets for the sourcing API.

Reads are open to any authenticated principal (Knox recruiter *or* the service
account); writes that the worker performs (candidates, enrichment, scores,
drafts) require the service account. RLS guarantees every queryset is already
tenant-scoped, so views never filter by tenant themselves.

NOTE: every viewset builds its queryset in ``get_queryset()`` rather than a
class-level ``queryset = Model.objects.all()`` attribute. The tenant
auto-filtering manager reads ``context.current_tenant`` *when the queryset is
constructed* — a class attribute is constructed once at import and would freeze
the filter to whichever tenant happened to be active then. Per-request
construction keeps the tenant scope live.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from zenlib_agentos.zenlib.reusable_apps.email_pipeline.authentication import (
    IsServiceAccount,
)

from ..models import (
    Candidate,
    Enrichment,
    LeadEdge,
    LinkedInAccount,
    OutreachDraft,
    Role,
    Score,
    SourcingRun,
    TargetCompany,
)
from ..serializers import (
    CandidateSerializer,
    EnrichmentSerializer,
    LeadEdgeSerializer,
    LeadSerializer,
    LinkedInAccountSerializer,
    OutreachDraftSerializer,
    RoleSerializer,
    ScoreSerializer,
    SourcingRunSerializer,
    TargetCompanySerializer,
)
from ..usage import role_cost


class _ServiceWriteMixin:
    """Reads for any authenticated principal; writes for the service account."""

    def get_permissions(self):
        if self.request.method in ("POST", "PUT", "PATCH", "DELETE"):
            return [IsServiceAccount()]
        return [IsAuthenticated()]


class RoleViewSet(viewsets.ModelViewSet):
    serializer_class = RoleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Role.objects.all()

    @action(detail=True, methods=["post"])
    def source(self, request, pk=None):
        """Kick off a sourcing run for this role.

        Creates the run record + seeds ``TargetCompany`` rows from the ICP.
        The Temporal worker (P7) picks up ``pending`` runs; until then this is
        the durable handoff point.
        """
        role = self.get_object()
        run = SourcingRun.objects.create(role=role, status=SourcingRun.Status.PENDING)
        for company in role.icp.get("target_companies", []):
            # The ICP may list a company as a bare name or a {name, linkedin_url}.
            spec = company if isinstance(company, dict) else {"name": company}
            if not spec.get("name"):
                continue
            TargetCompany.objects.get_or_create(
                role=role,
                name=spec["name"],
                defaults={
                    "linkedin_url": spec.get("linkedin_url", ""),
                    "source": TargetCompany.Source.SEED,
                    "depth": 0,
                },
            )
        return Response(
            SourcingRunSerializer(run).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get"])
    def leads(self, request, pk=None):
        """Ranked leads for this role: profile + score + provenance.

        Filters live in a single ``Score`` join (so verdict/min-score always
        refer to *this role's* score row), then ordered by score descending.
        """
        role = self.get_object()
        match = {"scores__role": role}
        if verdict := request.query_params.get("verdict"):
            match["scores__verdict"] = verdict
        if min_score := request.query_params.get("min_score"):
            match["scores__score__gte"] = int(min_score)

        candidates = (
            Candidate.objects.filter(**match)
            .prefetch_related("scores", "inbound_edges__from_company")
            .distinct()
        )
        ranked = sorted(
            candidates,
            key=lambda c: next(
                (s.score for s in c.scores.all() if s.role_id == role.id), 0
            ),
            reverse=True,
        )
        data = LeadSerializer(ranked, many=True, context={"role_id": role.id}).data
        return Response(data)

    @action(detail=True, methods=["get"])
    def costs(self, request, pk=None):
        """Estimated spend for this role (Brightdata + Claude + outreach)."""
        return Response(role_cost(self.get_object()))


class TargetCompanyViewSet(viewsets.ModelViewSet):
    serializer_class = TargetCompanySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return TargetCompany.objects.all()

    def create(self, request, *args, **kwargs):
        """Idempotent upsert on (role, name) — the natural key behind the
        unique constraint — so re-scanning a company doesn't 500."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        obj, created = TargetCompany.objects.update_or_create(
            role=v["role"], name=v["name"],
            defaults={k: val for k, val in v.items()
                      if k not in ("role", "name")},
        )
        out = self.get_serializer(obj).data
        return Response(
            out, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )


class SourcingRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SourcingRunSerializer

    def get_queryset(self):
        return SourcingRun.objects.all()

    def get_permissions(self):
        if self.action == "finalize":
            return [IsServiceAccount()]
        return [IsAuthenticated()]

    _COUNTERS = ("scanned_companies", "found_candidates", "fit_candidates",
                 "drafted", "budget_used")
    _TERMINAL = (SourcingRun.Status.COMPLETED, SourcingRun.Status.FAILED,
                 SourcingRun.Status.BUDGET_EXHAUSTED)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        """Worker call: set the run's final status + counters."""
        run = self.get_object()
        new_status = request.data.get("status")
        if new_status:
            run.status = new_status
        for field_name in self._COUNTERS:
            if field_name in request.data:
                setattr(run, field_name, int(request.data[field_name]))
        if run.status == SourcingRun.Status.RUNNING and run.started_at is None:
            run.started_at = timezone.now()
        if run.status in self._TERMINAL and run.finished_at is None:
            run.finished_at = timezone.now()
        run.save()
        return Response(self.get_serializer(run).data)


class CandidateViewSet(_ServiceWriteMixin, viewsets.ModelViewSet):
    serializer_class = CandidateSerializer

    def get_queryset(self):
        return Candidate.objects.all()

    def create(self, request, *args, **kwargs):
        """Idempotent upsert keyed on ``linkedin_url`` within the tenant."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        url = serializer.validated_data["linkedin_url"]
        defaults = {k: v for k, v in serializer.validated_data.items()
                    if k != "linkedin_url"}
        obj, created = Candidate.objects.update_or_create(
            linkedin_url=url, defaults=defaults
        )
        out = self.get_serializer(obj).data
        return Response(
            out, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )


class EnrichmentViewSet(_ServiceWriteMixin, viewsets.ModelViewSet):
    serializer_class = EnrichmentSerializer

    def get_queryset(self):
        return Enrichment.objects.all()

    def create(self, request, *args, **kwargs):
        """Upsert the (one) enrichment snapshot per candidate."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        obj, created = Enrichment.objects.update_or_create(
            candidate=v["candidate"],
            defaults={k: val for k, val in v.items() if k != "candidate"},
        )
        out = self.get_serializer(obj).data
        return Response(
            out, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )


class ScoreViewSet(_ServiceWriteMixin, viewsets.ModelViewSet):
    serializer_class = ScoreSerializer

    def get_queryset(self):
        return Score.objects.all()

    def create(self, request, *args, **kwargs):
        """Upsert one score per (candidate, role)."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        obj, created = Score.objects.update_or_create(
            candidate=v["candidate"], role=v["role"],
            defaults={k: val for k, val in v.items()
                      if k not in ("candidate", "role")},
        )
        out = self.get_serializer(obj).data
        return Response(
            out, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )


class LeadEdgeViewSet(_ServiceWriteMixin, viewsets.ModelViewSet):
    serializer_class = LeadEdgeSerializer

    def get_queryset(self):
        return LeadEdge.objects.all()

    # Natural key for a provenance edge — idempotent so re-running a monitor
    # matches the same edge instead of duplicating it.
    _EDGE_KEY = ("role", "to_candidate", "kind", "from_company",
                 "from_candidate", "depth")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        lookup = {k: v.get(k) for k in self._EDGE_KEY}
        extra = {k: val for k, val in v.items() if k not in lookup}
        obj, created = LeadEdge.objects.get_or_create(defaults=extra, **lookup)
        out = self.get_serializer(obj).data
        return Response(
            out, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )


class LinkedInAccountViewSet(viewsets.ViewSet):
    """The tenant's single connected LinkedIn account — Gojiberry-style."""

    permission_classes = [IsAuthenticated]

    def list(self, request):
        acct = LinkedInAccount.objects.first()
        data = LinkedInAccountSerializer(acct).data if acct else None
        return Response({"account": data})

    @action(detail=False, methods=["post"])
    def connect(self, request):
        acct, _ = LinkedInAccount.objects.get_or_create(
            defaults={"daily_invite_limit": request.data.get("daily_limit", 20)}
        )
        acct.connect(
            account_name=request.data.get("account_name", "LinkedIn"),
            session_cookie=request.data.get("session_cookie", ""),
            by=getattr(request.user, "username", "recruiter"),
        )
        return Response(LinkedInAccountSerializer(acct).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def verify(self, request):
        acct = LinkedInAccount.objects.first()
        if acct is None:
            raise ValidationError("no account connected")
        acct.verify()
        return Response(LinkedInAccountSerializer(acct).data)


class OutreachDraftViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachDraftSerializer

    def get_queryset(self):
        return OutreachDraft.objects.all()

    def get_permissions(self):
        # Worker creates drafts; recruiter approves/rejects/reads.
        if self.action in ("create", "destroy"):
            return [IsServiceAccount()]
        return [IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        """Idempotent upsert on (candidate, role, channel) — one draft per channel,
        safe to re-run sourcing or retry Temporal activities."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        lookup = {k: v[k] for k in ("candidate", "role", "channel")}
        extra = {k: val for k, val in v.items() if k not in lookup}
        obj, created = OutreachDraft.objects.get_or_create(defaults=extra, **lookup)
        out = self.get_serializer(obj).data
        return Response(
            out, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        draft = self.get_object()
        by = getattr(request.user, "username", "recruiter")
        try:
            draft.approve(by=by)
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else str(exc)
            raise ValidationError(detail) from exc
        return Response(self.get_serializer(draft).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        draft = self.get_object()
        reason = request.data.get("reason", "")
        try:
            draft.reject(reason=reason)
        except DjangoValidationError as exc:
            raise ValidationError(str(exc)) from exc
        return Response(self.get_serializer(draft).data)
