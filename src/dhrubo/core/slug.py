"""Filesystem-safe slug helpers (shared across agents and the exporter).

A slug is a lowercase-ish ASCII representation of an arbitrary
input string (typically a URL or path) that is safe to use as a
filename or directory name. The character allowlist is:

    ``[A-Za-z0-9-_.]``

Anything outside that set is replaced with a single underscore.
Leading/trailing underscores are stripped, runs of underscores
are collapsed, and the result is truncated to 80 chars (an
arbitrary sane bound).

Centralized here so the page indexer, the exporter, and any
future caller all agree on the same shape.
"""

from __future__ import annotations

import re

_KEEP = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
_MAX_LEN = 80
_COLLAPSE_RX = re.compile(r"_+")
_SLUGIFY_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SLUGIFY_TRIM = re.compile(r"^-+|-+$")


def safe_slug(value: str) -> str:
    """Return a filesystem-safe slug from ``value``.

    >>> safe_slug("https://example.com/")
    'https___example.com_'
    >>> safe_slug("Hello, World!")
    'Hello_World_'
    >>> safe_slug("")
    'report'
    """
    if not value:
        return "report"
    raw = "".join(c if c in _KEEP else "_" for c in value)
    collapsed = _COLLAPSE_RX.sub("_", raw).strip("_")
    return collapsed[:_MAX_LEN] or "report"


def slugify(value: str, *, max_len: int = 48) -> str:
    """Return a lowercase, hyphenated slug from ``value`` (issue-id style).

    Intended for building stable, human-readable identifiers
    (e.g. ``"Missing meta description"`` →
    ``"missing-meta-description"``). Used by M10's issue ``id``
    builder so diffs stay stable across runs.

    >>> slugify("Missing meta description!")
    'missing-meta-description'
    >>> slugify("  HTTPS   not   enforced  ")
    'https-not-enforced'
    >>> slugify("")
    'issue'
    """
    if not value:
        return "issue"
    lowered = value.lower()
    hyphenated = _SLUGIFY_NON_ALNUM.sub("-", lowered)
    trimmed = _SLUGIFY_TRIM.sub("", hyphenated)
    return trimmed[:max_len] or "issue"


__all__ = ["safe_slug", "slugify"]
