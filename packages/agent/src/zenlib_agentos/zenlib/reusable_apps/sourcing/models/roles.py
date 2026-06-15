"""Role (the opening + ICP), the sourcing run, and companies we monitor.

A ``Role`` carries the recruiter's ICP and the LLM-derived ``strategy``
(target companies/titles/rubric). A ``SourcingRun`` is one durable crawl of
the lead graph for a role. ``TargetCompany`` is a node on the company side of
that graph — seeded by the recruiter or discovered as a lead's prev employer.
"""

from __future__ import annotations

from django.db import models
from zenlib.reusable_apps.multitenant.models import ActivityTenantBaseModel


class Role(ActivityTenantBaseModel):
    """An opening Jill sources for. ``icp`` is recruiter input; ``strategy`` is
    Jill's plan derived from it."""

    class Status(models.TextChoices):
        DRAFT = "draft"
        SOURCING = "sourcing"
        PAUSED = "paused"
        CLOSED = "closed"

    title = models.CharField(max_length=200)
    # ICP: {must_have_skills[], nice_to_have_skills[], seniority, locations[],
    #       target_companies: [{name, linkedin_url}]}
    icp = models.JSONField(default=dict, blank=True)
    # Strategy (LLM-derived): {target_companies[], target_titles[], rubric}
    strategy = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT
    )

    class Meta(ActivityTenantBaseModel.Meta):
        db_table = "sourcing_role"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.title


class SourcingRun(ActivityTenantBaseModel):
    """One durable crawl of the lead graph for a role."""

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        BUDGET_EXHAUSTED = "budget_exhausted"

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    temporal_workflow_id = models.CharField(max_length=255, blank=True)

    scanned_companies = models.PositiveIntegerField(default=0)
    found_candidates = models.PositiveIntegerField(default=0)
    fit_candidates = models.PositiveIntegerField(default=0)
    drafted = models.PositiveIntegerField(default=0)
    budget_used = models.PositiveIntegerField(default=0)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta(ActivityTenantBaseModel.Meta):
        db_table = "sourcing_run"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"run#{self.pk} for {self.role_id} ({self.status})"


class TargetCompany(ActivityTenantBaseModel):
    """A company node we monitor for recent joiners."""

    class Source(models.TextChoices):
        SEED = "seed"  # recruiter-provided
        PREV_EMPLOYER = "prev_employer"  # discovered via a lead's history

    role = models.ForeignKey(
        Role, on_delete=models.CASCADE, related_name="targets"
    )
    name = models.CharField(max_length=200)
    linkedin_url = models.CharField(max_length=400, blank=True)
    source = models.CharField(
        max_length=16, choices=Source.choices, default=Source.SEED
    )
    depth = models.PositiveSmallIntegerField(default=0)
    last_scanned_at = models.DateTimeField(null=True, blank=True)
    # For source=prev_employer: the lead whose history surfaced this company.
    # String ref avoids a circular import with candidates.py.
    discovered_from = models.ForeignKey(
        "sourcing.Candidate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="surfaced_companies",
    )

    class Meta(ActivityTenantBaseModel.Meta):
        db_table = "sourcing_target_company"
        ordering = ("depth", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "role", "name"],
                name="uniq_target_company_per_role",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} (d{self.depth})"
