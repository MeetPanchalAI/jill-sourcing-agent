"""The people side of the lead graph: candidates, provenance edges, profiles.

``Candidate`` is the dedupe anchor — unique on ``(tenant, linkedin_url)`` so the
same person discovered via three different paths collapses to one row. Each
discovery records a ``LeadEdge`` (the "lead sources" provenance: recent_joiner |
prev_employer | network). ``Enrichment`` is the scraped profile snapshot Jill
scores against.
"""

from __future__ import annotations

from django.db import models
from zenlib.reusable_apps.multitenant.models import ActivityTenantBaseModel

from .roles import Role, SourcingRun, TargetCompany


class Candidate(ActivityTenantBaseModel):
    """A discovered person. Deduped per tenant by LinkedIn URL."""

    linkedin_url = models.CharField(max_length=400)
    full_name = models.CharField(max_length=200, blank=True)
    headline = models.CharField(max_length=400, blank=True)
    current_company = models.CharField(max_length=200, blank=True)
    current_title = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=200, blank=True)
    started_current_role_at = models.DateField(null=True, blank=True)
    first_seen_run = models.ForeignKey(
        SourcingRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="discovered_candidates",
    )

    class Meta(ActivityTenantBaseModel.Meta):
        db_table = "sourcing_candidate"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "linkedin_url"],
                name="uniq_candidate_per_tenant",
            )
        ]

    def __str__(self) -> str:
        return self.full_name or self.linkedin_url


class LeadEdge(ActivityTenantBaseModel):
    """A provenance edge: how a candidate entered the funnel.

    Exactly one origin is set depending on ``kind``:
      * recent_joiner / prev_employer → ``from_company``
      * network                       → ``from_candidate``
    """

    class Kind(models.TextChoices):
        RECENT_JOINER = "recent_joiner"
        PREV_EMPLOYER = "prev_employer"
        NETWORK = "network"

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="edges")
    run = models.ForeignKey(
        SourcingRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="edges",
    )
    to_candidate = models.ForeignKey(
        Candidate, on_delete=models.CASCADE, related_name="inbound_edges"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    from_company = models.ForeignKey(
        TargetCompany,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="emitted_edges",
    )
    from_candidate = models.ForeignKey(
        Candidate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outbound_edges",
    )
    depth = models.PositiveSmallIntegerField(default=0)
    # For network edges we record how the cohort was approximated (e.g.
    # "shared_company", "shared_school") since Brightdata rarely exposes
    # raw connections. See constraints.md / plan.md decision log.
    method = models.CharField(max_length=64, blank=True)

    class Meta(ActivityTenantBaseModel.Meta):
        db_table = "sourcing_lead_edge"
        ordering = ("depth", "created_at")

    def __str__(self) -> str:
        return f"{self.kind}->{self.to_candidate_id} (d{self.depth})"


class Enrichment(ActivityTenantBaseModel):
    """The current scraped profile snapshot for a candidate."""

    candidate = models.OneToOneField(
        Candidate, on_delete=models.CASCADE, related_name="enrichment"
    )
    raw = models.JSONField(default=dict, blank=True)
    # Parsed, normalized fields the scorer reads.
    experiences = models.JSONField(default=list, blank=True)  # [{company,title,...}]
    skills = models.JSONField(default=list, blank=True)
    fetched_at = models.DateTimeField()

    class Meta(ActivityTenantBaseModel.Meta):
        db_table = "sourcing_enrichment"
        ordering = ("-fetched_at",)

    def __str__(self) -> str:
        return f"enrichment<{self.candidate_id}>"
