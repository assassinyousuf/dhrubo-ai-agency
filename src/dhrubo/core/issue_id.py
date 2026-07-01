"""Stable per-issue identity for diff/runs (M10).

Every reviewer issue carries an ``id`` field. We populate it
deterministically from the issue's title + body so that two runs
of the audit on the same page produce stable ids — even if the
LLM rewords ``detail`` between runs (the content hash catches
content shifts but the slug prefix stays human-readable).

Identity for diffing is then ``id`` first (preferred) with a
fallback to ``(severity, title, detail)`` when ``id`` is missing
on either side (e.g. legacy data.json from a pre-M10 run).
"""

from __future__ import annotations

import hashlib
from typing import Any

from dhrubo.core.slug import slugify

_HASH_LEN = 8


def compute_issue_id(
    *,
    title: str,
    detail: str,
    severity: str,
) -> str:
    """Build a stable ``id`` from the issue's content.

    Format: ``<slug-of-title>:<sha1(title|detail|severity)[:8]>``.

    >>> compute_issue_id(title="Missing meta description", detail="…", severity="major")
    'missing-meta-description:…'
    """
    prefix = slugify(title or "issue")
    payload = f"{title or ''}|{detail or ''}|{severity or ''}"
    digest = hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"{prefix}:{digest[:_HASH_LEN]}"


def populate_issue_ids(payload: Any) -> Any:
    """Walk a reviewer sub-report payload and back-fill missing ``id``s.

    Mutates a shallow copy of each issue dict in place. Returns the
    same payload structure with ``id`` populated on every issue.

    The walker recognises two shapes:

    1. A flat ``SeoReport``-style payload: ``{"score": ..., "issues":
       [{"severity": ..., "title": ..., "detail": ..., ...}, ...]}``.
    2. A nested multi-page payload: ``{"0": {...shape 1...}, "1": ...}``
       (the format the report writer's ``_collect_multi_page_payloads``
       returns).
    """
    if isinstance(payload, dict):
        if "issues" in payload and isinstance(payload["issues"], list):
            _populate_list(payload["issues"])
            return payload
        for value in payload.values():
            populate_issue_ids(value)
    elif isinstance(payload, list):
        for item in payload:
            populate_issue_ids(item)
    return payload


def _populate_list(issues: list[dict[str, Any]]) -> None:
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if issue.get("id"):
            continue
        issue["id"] = compute_issue_id(
            title=str(issue.get("title", "")),
            detail=str(issue.get("detail", "")),
            severity=str(issue.get("severity", "")),
        )


__all__ = ["compute_issue_id", "populate_issue_ids"]
