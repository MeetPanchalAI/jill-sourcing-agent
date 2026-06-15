"""Outreach delivery — mockable, no auto-send (C17, C18).

``MockDeliverer`` records sends and makes no network call; ``LiveDeliverer`` is
key-gated. Neither will send a draft that hasn't been approved — the approval
gate is enforced both here (worker side) and on the web-py model state machine.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger("jill.outreach")


class NotApproved(Exception):
    """Raised when delivery is attempted on a draft that isn't approved."""


@runtime_checkable
class Deliverer(Protocol):
    def send(self, *, channel: str, to: str, subject: str, body: str) -> dict: ...


class MockDeliverer:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, *, channel: str, to: str, subject: str = "", body: str) -> dict:
        record = {"channel": channel, "to": to, "subject": subject}
        self.sent.append(record)
        # PII-safe: log channel + recipient id + length, never the body text.
        logger.info("outreach.deliver[mock] %s -> %s (%d chars)", channel, to,
                    len(body))
        return {"status": "sent", "provider": "mock"}


def deliver_if_approved(deliverer: Deliverer, draft: dict, *, to: str) -> dict:
    """Send a draft only if it is in the ``approved`` state.

    Defense in depth: the web-py model already forbids ``draft → sent`` without
    approval; this refuses the same transition on the worker side."""
    status = draft.get("status")
    if status != "approved":
        raise NotApproved(f"cannot send a draft in status '{status}'; approval "
                          "required")
    return deliverer.send(
        channel=draft["channel"], to=to,
        subject=draft.get("subject", ""), body=draft["body"],
    )
