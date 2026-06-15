"""DRF serializers for the sourcing API."""

from __future__ import annotations

from rest_framework import serializers

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


class LinkedInAccountSerializer(serializers.ModelSerializer):
    """Public view of the connected account — the session cookie is never
    serialized."""

    invites_remaining = serializers.SerializerMethodField()

    class Meta:
        model = LinkedInAccount
        fields = [
            "id", "account_name", "status", "daily_invite_limit",
            "invites_sent_today", "invites_remaining", "last_verified_at",
            "connected_by",
        ]
        read_only_fields = fields

    def get_invites_remaining(self, obj) -> int:
        return obj.invites_remaining


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "title", "icp", "strategy", "status", "created_at"]
        read_only_fields = ["id", "strategy", "created_at"]


class TargetCompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = TargetCompany
        fields = [
            "id", "role", "name", "linkedin_url", "source", "depth",
            "last_scanned_at", "discovered_from",
        ]
        read_only_fields = ["id", "last_scanned_at"]


class SourcingRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourcingRun
        fields = [
            "id", "role", "status", "temporal_workflow_id",
            "scanned_companies", "found_candidates", "fit_candidates",
            "drafted", "budget_used", "started_at", "finished_at",
        ]
        read_only_fields = fields


class EnrichmentSerializer(serializers.ModelSerializer):
    # Drop the implicit OneToOne UniqueValidator so the view can upsert; the
    # manager (passed, not pre-evaluated) still scopes existence to the tenant.
    candidate = serializers.PrimaryKeyRelatedField(
        queryset=Enrichment._meta.get_field("candidate").related_model._default_manager,
        validators=[],
    )

    class Meta:
        model = Enrichment
        fields = ["id", "candidate", "raw", "experiences", "skills", "fetched_at"]
        read_only_fields = ["id"]


class ScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Score
        fields = [
            "id", "candidate", "role", "score", "verdict", "summary", "criteria",
            "reasons", "drop_reason", "model",
        ]
        read_only_fields = ["id"]


class CandidateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Candidate
        fields = [
            "id", "linkedin_url", "full_name", "headline", "current_company",
            "current_title", "location", "started_current_role_at",
            "first_seen_run",
        ]
        read_only_fields = ["id"]


class LeadEdgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeadEdge
        fields = [
            "id", "role", "run", "to_candidate", "kind", "from_company",
            "from_candidate", "depth", "method",
        ]
        read_only_fields = ["id"]


class OutreachDraftSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachDraft
        fields = [
            "id", "candidate", "role", "channel", "subject", "body",
            "status", "approved_by", "reject_reason", "sent_at",
        ]
        read_only_fields = ["id", "status", "approved_by", "sent_at"]


class LeadSerializer(serializers.ModelSerializer):
    """A candidate surfaced as a ranked lead: profile + score + provenance."""

    score = serializers.SerializerMethodField()
    verdict = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()
    criteria = serializers.SerializerMethodField()
    reasons = serializers.SerializerMethodField()
    provenance = serializers.SerializerMethodField()

    class Meta:
        model = Candidate
        fields = [
            "id", "linkedin_url", "full_name", "headline", "current_company",
            "current_title", "score", "verdict", "summary", "criteria",
            "reasons", "provenance",
        ]

    def _score_for_role(self, obj):
        role_id = self.context.get("role_id")
        return next(
            (s for s in obj.scores.all() if str(s.role_id) == str(role_id)), None
        )

    def get_score(self, obj):
        s = self._score_for_role(obj)
        return s.score if s else None

    def get_verdict(self, obj):
        s = self._score_for_role(obj)
        return s.verdict if s else None

    def get_summary(self, obj):
        s = self._score_for_role(obj)
        return s.summary if s else ""

    def get_criteria(self, obj):
        s = self._score_for_role(obj)
        return s.criteria if s else []

    def get_reasons(self, obj):
        s = self._score_for_role(obj)
        return s.reasons if s else []

    def get_provenance(self, obj):
        return [
            {
                "kind": e.kind,
                "depth": e.depth,
                "from_company": e.from_company.name if e.from_company else None,
                "from_candidate": e.from_candidate_id,
                "method": e.method or None,
            }
            for e in obj.inbound_edges.all()
        ]
