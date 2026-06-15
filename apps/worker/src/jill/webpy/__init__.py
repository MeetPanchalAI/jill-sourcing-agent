"""web-py service API client."""

from __future__ import annotations

from ..config import Settings, get_settings
from .client import Upserted, WebPyClient, WebPyError
from .fake import FakeWebPy

__all__ = ["FakeWebPy", "Upserted", "WebPyClient", "WebPyError", "get_webpy_client"]


def get_webpy_client(tenant_id: int | str, settings: Settings | None = None
                     ) -> WebPyClient:
    settings = settings or get_settings()
    return WebPyClient(
        base_url=settings.webpy_base_url,
        service_token=settings.service_token,
        tenant_id=tenant_id,
    )
