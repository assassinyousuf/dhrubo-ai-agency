"""`DiffTool` — diff two audit sub-report payloads (M10).

Pure-function tool: takes two ``sub_reports`` dicts (the structured
per-lens payloads produced by the report writer's
``_collect_multi_page_payloads`` for single- or multi-page runs)
and emits a diff dict that the report writer can render into the
``## Diff vs <run_id>`` H2 section.

The diff shape is::

    {
        "run_id_a": str,
        "run_id_b": str,
        "added": [{"lens": str, "page": str | None,
                   "issue": {...full issue dict...}}],
        "removed": [...],
        "severity_changed": [{"lens", "page", "id", "title",
                              "severity_a", "severity_b"}],
        "score_changed": [{"lens", "page", "score_a", "score_b",
                           "delta"}],
        "summary": "<human-readable summary line>",
    }

Identity for issues is ``id`` first (preferred — populated by
``LLMAgent._to_result`` from M10's content hash). Fallback when
``id`` is missing on either side: ``(severity, title, detail)``.

For multi-page payloads (``{"0": {...}, "1": {...}}``), each diff
row carries a ``page`` key (``"0"`` = first page, etc.) so the
report writer can group changes per page.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from dhrubo.tools.tool_interface import Tool, ToolContext, ToolParameter, ToolResult

# The six lenses the audit produces (multi-page keeps the same
# keys inside each per-page dict).
_LENS_KEYS: tuple[str, ...] = (
    "seo_report",
    "ui_report",
    "performance_report",
    "a11y_report",
    "security_report",
    "branding_report",
)


class DiffParams(BaseModel):
    """Inputs for :class:`DiffTool`."""

    run_id_a: str = Field(min_length=1, max_length=512)
    run_id_b: str = Field(min_length=1, max_length=512)
    sub_reports_a: dict[str, Any] = Field(default_factory=dict)
    sub_reports_b: dict[str, Any] = Field(default_factory=dict)


class DiffTool(Tool[DiffParams]):
    """Compute the diff between two audit sub-report payloads."""

    name: ClassVar[str] = "diff"
    description: ClassVar[str] = (
        "Compare two sub-report payloads (single- or multi-page) and emit "
        "added/removed/severity_changed/score_changed diffs with a one-line "
        "summary. Pure local compute — no I/O, no retries."
    )
    parameters: ClassVar[tuple[ToolParameter, ...]] = (
        ToolParameter("run_id_a", "string", description="Older run id (e.g. timestamp+host)."),
        ToolParameter("run_id_b", "string", description="Newer run id."),
        ToolParameter(
            "sub_reports_a",
            "object",
            required=False,
            description="Older run's structured sub-reports dict.",
        ),
        ToolParameter(
            "sub_reports_b",
            "object",
            required=False,
            description="Newer run's structured sub-reports dict.",
        ),
    )
    params_model: ClassVar[type[BaseModel]] = DiffParams

    @staticmethod
    def is_available() -> bool:
        """Pure function — no deps, always available."""
        return True

    async def _do_call(
        self,
        *,
        run_id_a: str,
        run_id_b: str,
        sub_reports_a: dict[str, Any],
        sub_reports_b: dict[str, Any],
    ) -> dict[str, Any]:
        return _diff(
            run_id_a=run_id_a,
            run_id_b=run_id_b,
            sub_reports_a=sub_reports_a or {},
            sub_reports_b=sub_reports_b or {},
        )

    async def run(self, params: DiffParams, ctx: ToolContext) -> ToolResult:
        try:
            data = await self._do_call(
                run_id_a=params.run_id_a,
                run_id_b=params.run_id_b,
                sub_reports_a=params.sub_reports_a,
                sub_reports_b=params.sub_reports_b,
            )
        except Exception as exc:
            return ToolResult.fail("diff", error=f"diff failed: {exc!r}")
        return ToolResult.ok("diff", data=data, summary=data.get("summary", ""))


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------


def _diff(
    *,
    run_id_a: str,
    run_id_b: str,
    sub_reports_a: dict[str, Any],
    sub_reports_b: dict[str, Any],
) -> dict[str, Any]:
    """Walk both payload shapes and emit the unified diff.

    Two payload shapes are supported:

    1. **Flat single-page** — ``{"seo_report": {...}, "ui_report": {...}, ...}``.
    2. **Multi-page** — ``{"0": {shape 1}, "1": {shape 1}, ...}`` (the
       structure produced by ``report_writer._collect_multi_page_payloads``).
    """
    flat_a = _flatten(sub_reports_a)
    flat_b = _flatten(sub_reports_b)

    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    severity_changed: list[dict[str, Any]] = []
    score_changed: list[dict[str, Any]] = []

    keys = sorted(set(flat_a) | set(flat_b))
    for key in keys:
        payload_a = flat_a.get(key) or {}
        payload_b = flat_b.get(key) or {}
        lens, page = _split_key(key)
        # ---- issues ----
        issues_a = _issues_of(payload_a)
        issues_b = _issues_of(payload_b)
        map_a = _index_issues(issues_a)
        map_b = _index_issues(issues_b)
        ids_a = set(map_a)
        ids_b = set(map_b)
        for new_id in ids_b - ids_a:
            added.append({"lens": lens, "page": page, "issue": map_b[new_id]})
        for old_id in ids_a - ids_b:
            removed.append({"lens": lens, "page": page, "issue": map_a[old_id]})
        for shared_id in ids_a & ids_b:
            ia = map_a[shared_id]
            ib = map_b[shared_id]
            if _severity_rank(ia.get("severity")) != _severity_rank(ib.get("severity")):
                severity_changed.append(
                    {
                        "lens": lens,
                        "page": page,
                        "id": shared_id,
                        "title": ib.get("title") or ia.get("title"),
                        "severity_a": ia.get("severity"),
                        "severity_b": ib.get("severity"),
                    }
                )
        # ---- scores ----
        score_a = _score_of(payload_a)
        score_b = _score_of(payload_b)
        if score_a is not None and score_b is not None and score_a != score_b:
            score_changed.append(
                {
                    "lens": lens,
                    "page": page,
                    "score_a": score_a,
                    "score_b": score_b,
                    "delta": score_b - score_a,
                }
            )

    added.sort(key=_row_sort_key)
    removed.sort(key=_row_sort_key)
    severity_changed.sort(key=_row_sort_key)
    score_changed.sort(key=_row_sort_key)

    summary = (
        f"{len(added)} added, {len(removed)} removed, "
        f"{len(severity_changed)} severity-changed, "
        f"{len(score_changed)} score-changed"
    )

    return {
        "run_id_a": run_id_a,
        "run_id_b": run_id_b,
        "added": added,
        "removed": removed,
        "severity_changed": severity_changed,
        "score_changed": score_changed,
        "summary": summary,
    }


def _flatten(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalise the two payload shapes to ``{(lens, page): sub_report}``.

    For single-page: ``{"seo_report": {...}}`` → ``{"seo_report||": {...}}``.
    For multi-page: ``{"0": {"seo_report": {...}}}`` → ``{"seo_report|0": {...}}``.
    The ``|`` separator keeps the key round-trippable through ``_split_key``.
    """
    out: dict[str, dict[str, Any]] = {}
    if not payload:
        return out
    # Multi-page detection: any value is itself a dict of *_report keys.
    if any(isinstance(v, dict) and any(_is_lens_key(k) for k in v) for v in payload.values()):
        for page, page_payload in payload.items():
            if not isinstance(page_payload, dict):
                continue
            for lens, sub in page_payload.items():
                if _is_lens_key(lens) and isinstance(sub, dict):
                    out[f"{lens}|{page}"] = sub
    else:
        # Flat single-page.
        for lens, sub in payload.items():
            if _is_lens_key(lens) and isinstance(sub, dict):
                out[f"{lens}|"] = sub
    return out


def _split_key(key: str) -> tuple[str, str | None]:
    lens, _, page = key.partition("|")
    return lens, (page or None)


def _is_lens_key(k: str) -> bool:
    return k in _LENS_KEYS


def _issues_of(sub: dict[str, Any]) -> list[dict[str, Any]]:
    raw = sub.get("issues")
    return [i for i in (raw or []) if isinstance(i, dict)]


def _index_issues(issues: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for issue in issues:
        iid = issue.get("id")
        if not iid:
            # Fallback identity — used only when id is missing (legacy data).
            iid = (
                f"fallback:{issue.get('severity', '')}|"
                f"{issue.get('title', '')}|{issue.get('detail', '')}"
            )
        out[iid] = issue
    return out


def _score_of(sub: dict[str, Any]) -> int | None:
    s = sub.get("score")
    return s if isinstance(s, int) else None


_SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2, "info": 3}


def _severity_rank(sev: Any) -> int:
    return _SEVERITY_RANK.get(str(sev), 99)


def _row_sort_key(row: dict[str, Any]) -> tuple[int, str, str]:
    return (
        _severity_rank(
            row.get("issue", {}).get("severity") or row.get("severity_b") or "info"
        ),
        str(row.get("lens", "")),
        str(row.get("page") or ""),
    )


__all__ = ["DiffParams", "DiffTool", "compute_diff"]


def compute_diff(
    *,
    run_id_a: str,
    run_id_b: str,
    sub_reports_a: dict[str, Any],
    sub_reports_b: dict[str, Any],
) -> dict[str, Any]:
    """Pure-function wrapper around :func:`_diff`.

    Public, no ``ToolContext`` required. Used by the standalone
    ``dhrubo diff`` subcommand (M11) which has no DAG, no
    ``safe_run`` plumbing, and no retry policy.

    >>> compute_diff(
    ...     run_id_a="a", run_id_b="b",
    ...     sub_reports_a={"seo_report": {"score": 80, "issues": []}},
    ...     sub_reports_b={"seo_report": {"score": 75, "issues": []}},
    ... )["summary"]
    '0 added, 0 removed, 0 severity-changed, 1 score-changed'
    """
    return _diff(
        run_id_a=run_id_a,
        run_id_b=run_id_b,
        sub_reports_a=sub_reports_a or {},
        sub_reports_b=sub_reports_b or {},
    )
