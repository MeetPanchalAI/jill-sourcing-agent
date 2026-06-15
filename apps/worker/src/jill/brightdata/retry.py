"""Bounded exponential backoff for scrape calls (C3).

``sleep`` is injectable so tests drive retries with zero wall-clock delay.
Only ``RETRYABLE`` errors are retried; everything else propagates immediately.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .errors import RETRYABLE

logger = logging.getLogger("jill.brightdata")


def call_with_retry[T](
    fn: Callable[[], T],
    *,
    max_attempts: int,
    base_delay: float,
    sleep: Callable[[float], None] = time.sleep,
    op: str = "scrape",
) -> T:
    """Call ``fn``; retry retryable failures up to ``max_attempts`` with
    exponential backoff (base_delay · 2^(n-1))."""
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except RETRYABLE as exc:
            if attempt == max_attempts:
                logger.warning("%s failed after %d attempts: %s", op, attempt,
                               type(exc).__name__)
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.info("%s retry %d/%d after %s (backoff %.2fs)", op, attempt,
                        max_attempts, type(exc).__name__, delay)
            sleep(delay)
    raise RuntimeError("call_with_retry requires max_attempts >= 1")
