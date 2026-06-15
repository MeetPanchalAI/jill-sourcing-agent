"""Judgment + action outputs: fit scores, outreach drafts, idempotency ledger."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from zenlib.reusable_apps.multitenant.models import ActivityTenantBaseModel

from .candidates import Candidate
from .roles import Role


class Score(ActivityTenantBaseModel):
    """Jill's fit verdict for a candidate against a role. One per (candidate, role)."""

    class Verdict(models.TextChoices):
        FIT = "fit"
        DROP = "drop"

    candidate = models.ForeignKey(
        Candidate, on_delete=models.CASCADE, related_name="scores"
    )
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="scores")
    score = models.PositiveSmallIntegerField(default=0)  # 0..100
    verdict = models.CharField(max_length=8, choices=Verdict.choices)
    summary = models.CharField(max_length=400, blank=True)  # one-line skimmable
    # Per-criterion breakdown: [{name, weight, status, detail}]
    criteria = models.JSONField(default=list, blank=True)
    reasons = models.JSONField(default=list, blank=True)  # grounded evidence
    drop_reason = models.CharField(max_length=400, blank=True)
    model = models.CharField(max_length=64, blank=True)  # llm id or "mock:rules"

    class Meta(ActivityTenantBaseModel.Meta):
        db_table = "sourcing_score"
        ordering = ("-score",)
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "candidate", "role"],
                name="uniq_score_per_candidate_role",
            )
        ]

    def __str__(self) -> str:
        return f"{self.verdict}:{self.score} c{self.candidate_id}"


class OutreachDraft(ActivityTenantBaseModel):
    """A staged invite. Cannot send without an explicit approve transition (C17)."""

    class Channel(models.TextChoices):
        LINKEDIN = "linkedin"
        EMAIL = "email"

    class Status(models.TextChoices):
        DRAFT = "draft"
        APPROVED = "approved"
        SENT = "sent"
        REJECTED = "rejected"

    candidate = models.ForeignKey(
        Candidate, on_delete=models.CASCADE, related_name="drafts"
    )
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="drafts")
    channel = models.CharField(max_length=12, choices=Channel.choices)
    subject = models.CharField(max_length=300, blank=True)  # email only
    body = models.TextField()
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT
    )
    approved_by = models.CharField(max_length=150, blank=True)
    reject_reason = models.CharField(max_length=400, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta(ActivityTenantBaseModel.Meta):
        db_table = "sourcing_outreach_draft"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "candidate", "role", "channel"],
                name="uniq_draft_per_candidate_role_channel",
            )
        ]

    # --- state machine -----------------------------------------------------

    def approve(self, by: str) -> None:
        if self.status != self.Status.DRAFT:
            raise ValidationError(
                f"can only approve a draft, not {self.status}", code="bad_transition"
            )
        self.status = self.Status.APPROVED
        self.approved_by = by
        self.save(update_fields=["status", "approved_by", "updated_at"])

    def reject(self, reason: str = "") -> None:
        if self.status not in (self.Status.DRAFT, self.Status.APPROVED):
            raise ValidationError(
                f"cannot reject from {self.status}", code="bad_transition"
            )
        self.status = self.Status.REJECTED
        self.reject_reason = reason
        self.save(update_fields=["status", "reject_reason", "updated_at"])

    def mark_sent(self) -> None:
        # The only path to SENT is from APPROVED — never directly from DRAFT.
        if self.status != self.Status.APPROVED:
            raise ValidationError(
                f"cannot send from {self.status}; approval required",
                code="bad_transition",
            )
        self.status = self.Status.SENT
        self.sent_at = timezone.now()
        self.save(update_fields=["status", "sent_at", "updated_at"])

    def __str__(self) -> str:
        return f"{self.channel} draft c{self.candidate_id} ({self.status})"


class IdemKey(ActivityTenantBaseModel):
    """Idempotency ledger. A key recorded here means its effect already ran."""

    key = models.CharField(max_length=255)
    kind = models.CharField(max_length=64, blank=True)
    ref = models.CharField(max_length=255, blank=True)  # entity it produced

    class Meta(ActivityTenantBaseModel.Meta):
        db_table = "sourcing_idem_key"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "key"], name="uniq_idem_key_per_tenant"
            )
        ]

    def __str__(self) -> str:
        return self.key
