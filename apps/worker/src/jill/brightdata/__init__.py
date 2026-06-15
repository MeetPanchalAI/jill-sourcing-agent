"""Brightdata: the only path to LinkedIn data (C1).

``get_client()`` returns the mock (fixtures) or live (real API) impl based on
``Settings.mode``. Default is mock — zero network, zero secrets.
"""

from __future__ import annotations

from ..config import Settings, get_settings
from .base import BrightdataClient
from .errors import (
    BrightdataAuthError,
    BrightdataError,
    BrightdataNotFound,
    BrightdataRateLimited,
    BrightdataServerError,
)
from .mock import MockBrightdataClient
from .types import EmployeeRef, Experience, Profile

__all__ = [
    "BrightdataAuthError",
    "BrightdataClient",
    "BrightdataError",
    "BrightdataNotFound",
    "BrightdataRateLimited",
    "BrightdataServerError",
    "EmployeeRef",
    "Experience",
    "Profile",
    "get_client",
]


def get_client(settings: Settings | None = None) -> BrightdataClient:
    settings = settings or get_settings()
    if settings.is_live:
        from .live import LiveBrightdataClient  # lazy: avoid httpx in mock paths

        return LiveBrightdataClient(settings)
    return MockBrightdataClient()
