"""The recruiter's connected LinkedIn account — Gojiberry-style.

One account per tenant. We store the session (the ``li_at`` cookie a browser
extension hands us), keep it alive with periodic ``verify()`` checks, and send
personalized connection invites through it — rate-limited to a safe daily cap.
The cookie is a secret; in production it lives encrypted / in a vault, never
echoed back to the browser.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from zenlib.reusable_apps.multitenant.models import ActivityTenantBaseModel


class LinkedInAccount(ActivityTenantBaseModel):
    class Status(models.TextChoices):
        CONNECTED = "connected"
        EXPIRED = "expired"
        DISCONNECTED = "disconnected"

    account_name = models.CharField(max_length=200, blank=True)
    # The session token (li_at). Secret — never returned by the API.
    session_cookie = models.CharField(max_length=2000, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DISCONNECTED
    )
    daily_invite_limit = models.PositiveSmallIntegerField(default=20)
    invites_sent_today = models.PositiveSmallIntegerField(default=0)
    invites_reset_on = models.DateField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    connected_by = models.CharField(max_length=150, blank=True)

    class Meta(ActivityTenantBaseModel.Meta):
        db_table = "sourcing_linkedin_account"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant"], name="uniq_linkedin_account_per_tenant"
            )
        ]

    def __str__(self) -> str:
        return f"{self.account_name or 'LinkedIn'} ({self.status})"

    # --- lifecycle ---------------------------------------------------------

    def connect(self, *, account_name: str, session_cookie: str, by: str) -> None:
        self.account_name = account_name
        self.session_cookie = session_cookie
        self.status = self.Status.CONNECTED
        self.connected_by = by
        self.last_verified_at = timezone.now()
        self.save()

    def verify(self) -> bool:
        """Keep-alive check. Mock: a stored cookie means the session is live.
        Live: ping LinkedIn ``/me`` and flip to EXPIRED on 401."""
        alive = bool(self.session_cookie)
        self.status = self.Status.CONNECTED if alive else self.Status.EXPIRED
        self.last_verified_at = timezone.now()
        self.save(update_fields=["status", "last_verified_at", "updated_at"])
        return alive

    # --- rate limiting -----------------------------------------------------

    def _roll_day(self) -> None:
        today = timezone.now().date()
        if self.invites_reset_on != today:
            self.invites_reset_on = today
            self.invites_sent_today = 0

    @property
    def invites_remaining(self) -> int:
        self._roll_day()
        return max(0, self.daily_invite_limit - self.invites_sent_today)

    def can_invite(self) -> bool:
        return self.status == self.Status.CONNECTED and self.invites_remaining > 0

    def record_invite(self) -> None:
        self._roll_day()
        self.invites_sent_today += 1
        self.save(update_fields=["invites_sent_today", "invites_reset_on",
                                 "updated_at"])
