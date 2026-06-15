"""Typed Brightdata errors so callers (and the retry layer) can distinguish
transient failures (retry) from terminal ones (give up)."""

from __future__ import annotations


class BrightdataError(Exception):
    """Base for all Brightdata client failures."""


class BrightdataRateLimited(BrightdataError):
    """HTTP 429 — back off and retry."""


class BrightdataServerError(BrightdataError):
    """HTTP 5xx — transient, retry."""


class BrightdataNotFound(BrightdataError):
    """HTTP 404 — terminal, do not retry."""


class BrightdataAuthError(BrightdataError):
    """Missing/invalid credentials — terminal, do not retry."""


# Exceptions worth retrying.
RETRYABLE = (BrightdataRateLimited, BrightdataServerError)
