"""Template filters for the sourcing dashboard."""

from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def pretty_name(value: str) -> str:
    """Turn a LinkedIn URL or slug into a readable label.

    ``https://www.linkedin.com/company/vapi-ai`` → ``Vapi Ai``
    ``https://www.linkedin.com/in/lat-ware-36169a1a`` → ``Lat Ware``
    A normal name (``Vapi``) is returned unchanged. Used so the UI never shows a
    raw URL where a name belongs."""
    s = (value or "").strip()
    if not s:
        return s
    if "linkedin.com/" not in s and "/" not in s:
        return s  # already a plain name

    slug = s.rstrip("/").split("/")[-1].split("?")[0]
    # Drop a trailing LinkedIn id hash (e.g. "lat-ware-36169a1a" → "lat-ware").
    parts = slug.split("-")
    if len(parts) > 1 and any(c.isdigit() for c in parts[-1]):
        parts = parts[:-1]
    label = " ".join(parts).replace("_", " ").strip()
    return label.title() if label else s
