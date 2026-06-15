"""Live outreach delivery — key-gated stub.

Shape only: a real deployment wires email (SMTP/provider API) and LinkedIn
invites (via Brightdata/automation) here. Engages only in live mode; refuses to
construct without the relevant credentials.
"""

from __future__ import annotations

import logging

from ..config import Settings

logger = logging.getLogger("jill.outreach")


class LiveDeliverer:
    def __init__(self, settings: Settings):
        # Real credentials (SMTP creds, LinkedIn automation key) would be
        # required here. Kept minimal since live delivery isn't exercised.
        self._s = settings

    def send(self, *, channel: str, to: str, subject: str = "", body: str) -> dict:
        if channel == "email":
            return self._send_email(to, subject, body)
        if channel == "linkedin":
            return self._send_linkedin(to, body)
        raise ValueError(f"unknown channel {channel!r}")

    def _send_email(self, to: str, subject: str, body: str) -> dict:
        raise NotImplementedError("wire an email provider for live email delivery")

    def _send_linkedin(self, to: str, body: str) -> dict:
        raise NotImplementedError("wire LinkedIn automation for live invites")
