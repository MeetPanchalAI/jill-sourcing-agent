"""Routes for ``/api/v1/sourcing/``."""

from rest_framework.routers import DefaultRouter

from .views import (
    CandidateViewSet,
    EnrichmentViewSet,
    LeadEdgeViewSet,
    LinkedInAccountViewSet,
    OutreachDraftViewSet,
    RoleViewSet,
    ScoreViewSet,
    SourcingRunViewSet,
    TargetCompanyViewSet,
)

router = DefaultRouter()
router.register(r"roles", RoleViewSet, basename="role")
router.register(r"targets", TargetCompanyViewSet, basename="target")
router.register(r"runs", SourcingRunViewSet, basename="run")
router.register(r"candidates", CandidateViewSet, basename="candidate")
router.register(r"enrichments", EnrichmentViewSet, basename="enrichment")
router.register(r"scores", ScoreViewSet, basename="score")
router.register(r"lead-edges", LeadEdgeViewSet, basename="lead-edge")
router.register(r"outreach", OutreachDraftViewSet, basename="outreach")
router.register(r"linkedin", LinkedInAccountViewSet, basename="linkedin")

urlpatterns = router.urls
