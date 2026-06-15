"""Outreach delivery."""

from __future__ import annotations

from ..config import Settings, get_settings
from .deliver import Deliverer, MockDeliverer, NotApproved, deliver_if_approved

__all__ = [
    "Deliverer",
    "MockDeliverer",
    "NotApproved",
    "deliver_if_approved",
    "get_deliverer",
]


def get_deliverer(settings: Settings | None = None) -> Deliverer:
    settings = settings or get_settings()
    if settings.is_live:
        from .live import LiveDeliverer  # lazy

        return LiveDeliverer(settings)
    return MockDeliverer()
