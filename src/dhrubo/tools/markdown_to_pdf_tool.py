"""`MarkdownToPdfTool` — render an audit Markdown report to PDF via WeasyPrint.

WeasyPrint is part of the optional ``[pdf]`` extra and depends on native
libraries (libpango, libcairo, libgdk-pixbuf, ...). When the package is
not importable on the host we **gracefully skip** the PDF write (same
shape as :class:`LighthouseTool`'s no-API-key fallback) so an audit
never fails purely because WeasyPrint isn't installed.

Design notes:

- **Markdown → HTML** uses the ``markdown`` package (``extensions=["tables",
  "fenced_code"]``) which the audit's tables-and-code-block reports
  require.
- **HTML → PDF** uses WeasyPrint with a minimal stylesheet (page size,
  margins, table styling, code-block styling, responsive ``img``).
- **Retry policy** is loaded from :mod:`dhrubo.config.loader` (the
  ``markdown_to_pdf`` entry in ``config/retry_policies.yaml``), falling
  back to :data:`dhrubo.core.retry.DEFAULT_RETRY`.
- **HTTP-level mocking seam**: ``_do_call`` is an instance method that
  callers can monkey-patch in tests. Production code calls
  ``HTML(...).write_pdf(target)`` directly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from dhrubo.config.loader import load_retry_policies
from dhrubo.core.errors import ToolError
from dhrubo.core.logger import get_logger
from dhrubo.core.retry import DEFAULT_RETRY, RetryConfig, retry_async
from dhrubo.tools.tool_interface import Tool, ToolContext, ToolParameter, ToolResult

_log = get_logger("tools.markdown_to_pdf")

# Minimal HTML wrapper. We control the CSS so the same template works on
# WeasyPrint ≥ 62 across Win / macOS / Linux without pulling in a theming
# library. Emoji rendering relies on the host's color emoji font
# (Segoe UI Emoji on Windows, Apple Color Emoji on macOS, Noto Color
# Emoji on Linux).
_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @page {{ size: {size}; margin: 18mm 16mm; }}
  body {{ font: 11pt/1.5 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif; color: #111; }}
  h1 {{ font-size: 22pt; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  h2 {{ font-size: 15pt; margin-top: 1.6em; color: #1f2937; }}
  h3 {{ font-size: 12pt; margin-top: 1.2em; color: #374151; }}
  code {{ background: #f3f4f6; padding: 0 4px; border-radius: 3px; font-family: ui-monospace, Consolas, monospace; }}
  pre  {{ background: #f3f4f6; padding: 8px 12px; border-radius: 4px; overflow: auto; }}
  table {{ border-collapse: collapse; width: 100%; margin: 0.6em 0; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
  th {{ background: #f9fafb; }}
  blockquote {{ border-left: 3px solid #e5e7eb; margin: 0.6em 0; padding: 4px 12px; color: #4b5563; }}
  img {{ max-width: 100%; height: auto; }}
  hr {{ border: none; border-top: 1px solid #e5e7eb; margin: 1.4em 0; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""

# A pre-shaped payload returned when WeasyPrint is not available. The
# exporter recognises this and short-circuits its PDF write.
_SKIP_PAYLOAD: dict[str, Any] = {
    "skipped": True,
    "reason": "weasyprint is not installed; run `pip install -e '.[pdf]'`",
    "output_path": None,
    "size_bytes": 0,
}


def _resolve_retry_policy(config_dir: Path | None = None) -> RetryConfig:
    """Return the ``markdown_to_pdf`` retry policy (or DEFAULT_RETRY on miss)."""
    if config_dir is None:
        return DEFAULT_RETRY
    try:
        policies = load_retry_policies(config_dir)
    except Exception as exc:  # pragma: no cover - bad config shouldn't break tool
        _log.warning("markdown_to_pdf.retry_policy_load_failed", extra={"error": str(exc)})
        return DEFAULT_RETRY
    return policies.get("markdown_to_pdf", DEFAULT_RETRY)


class MarkdownToPdfParams(BaseModel):
    """Inputs for :class:`MarkdownToPdfTool`."""

    markdown: str = Field(min_length=1)
    output_path: str = Field(min_length=1, max_length=4096)
    base_url: str | None = Field(default=None, max_length=4096)
    page_size: Literal["a4", "letter"] = "a4"
    title: str = Field(default="Audit Report", min_length=1, max_length=512)


class MarkdownToPdfTool(Tool[MarkdownToPdfParams]):
    """Render Markdown to PDF using WeasyPrint (or skip if unavailable)."""

    name: ClassVar[str] = "markdown_to_pdf"
    description: ClassVar[str] = (
        "Render a Markdown string to a styled PDF using WeasyPrint. "
        "Returns a skip payload if WeasyPrint is not installed."
    )
    parameters: ClassVar[tuple[ToolParameter, ...]] = (
        ToolParameter("markdown", "string", description="Markdown source text."),
        ToolParameter("output_path", "string", description="Absolute file path to write the PDF to."),
        ToolParameter("base_url", "string", required=False, description="Base URL for resolving relative refs."),
        ToolParameter("page_size", "a4|letter", required=False),
        ToolParameter("title", "string", required=False),
    )
    params_model: ClassVar[type[BaseModel]] = MarkdownToPdfParams

    def __init__(self, *, config_dir: Path | None = None) -> None:
        self._config_dir = config_dir
        self._retry_policy: RetryConfig = _resolve_retry_policy(config_dir)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @staticmethod
    def is_available() -> bool:
        """Return True if ``weasyprint`` and ``markdown`` are importable."""
        try:
            import markdown  # type: ignore[import-untyped]  # noqa: F401
            import weasyprint  # type: ignore[import-not-found]  # noqa: F401
        except Exception:
            return False
        return True

    # ------------------------------------------------------------------
    # Hooks for tests / future enhancements
    # ------------------------------------------------------------------

    async def _do_call(
        self,
        *,
        html: str,
        base_url: str | None,
        output_path: str,
    ) -> int:
        """Render ``html`` to ``output_path``. Returns bytes written.

        Tests monkey-patch this seam to avoid booting the WeasyPrint
        stack. Production code builds a ``weasyprint.HTML`` and writes
        directly to ``output_path``.
        """
        from weasyprint import HTML  # optional dep

        html_obj = HTML(string=html, base_url=base_url)
        html_obj.write_pdf(output_path)
        return os.path.getsize(output_path)

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------

    async def run(self, params: MarkdownToPdfParams, ctx: ToolContext) -> ToolResult:
        if not self.is_available():
            _log.info(
                "markdown_to_pdf.skipped_unavailable",
                extra={
                    "tool": self.name,
                    "output_path": params.output_path,
                    "requester": ctx.requester_role,
                },
            )
            return ToolResult.ok(
                self.name,
                data=dict(_SKIP_PAYLOAD),
                skipped=True,
                output_path=None,
            )

        # Build the HTML shell (cheap; markdown + format). Heavy lifting
        # (the actual render) is wrapped in retry_async so a transient
        # font-cache race triggers one backoff, not a hard failure.
        # The markdown lib is an optional dep too — treat any ImportError
        # here the same as WeasyPrint being missing: skip-with-info.
        try:
            import markdown as _md  # optional dep
        except ImportError as exc:
            _log.info(
                "markdown_to_pdf.skipped_missing_markdown",
                extra={
                    "tool": self.name,
                    "output_path": params.output_path,
                    "requester": ctx.requester_role,
                    "error": str(exc),
                },
            )
            return ToolResult.ok(
                self.name,
                data=dict(_SKIP_PAYLOAD, reason="markdown package not installed"),
                skipped=True,
                output_path=None,
            )

        try:
            html_body = _md.markdown(
                params.markdown,
                extensions=["tables", "fenced_code"],
            )
        except Exception as exc:  # pragma: no cover - markdown lib always succeeds on str
            raise ToolError(
                f"Markdown rendering failed: {exc!r}",
                context={"tool": self.name, "requester": ctx.requester_role},
                cause=exc,
            ) from exc

        html = _HTML_TEMPLATE.format(
            title=_html_escape(params.title),
            size=params.page_size.upper(),
            body=html_body,
        )

        _log.info(
            "markdown_to_pdf.start",
            extra={
                "tool": self.name,
                "output_path": params.output_path,
                "page_size": params.page_size,
                "requester": ctx.requester_role,
            },
        )

        try:
            size_bytes = await retry_async(
                lambda: self._do_call(
                    html=html,
                    base_url=params.base_url,
                    output_path=params.output_path,
                ),
                policy=self._retry_policy,
                op_name="markdown_to_pdf.render",
                retriable=(Exception,),
            )
        except Exception as exc:
            raise ToolError(
                f"PDF rendering failed: {exc!r}",
                context={
                    "tool": self.name,
                    "output_path": params.output_path,
                    "requester": ctx.requester_role,
                },
                cause=exc,
            ) from exc

        return ToolResult.ok(
            self.name,
            data={
                "skipped": False,
                "reason": None,
                "output_path": params.output_path,
                "size_bytes": int(size_bytes),
                "page_size": params.page_size,
            },
            output_path=params.output_path,
            size_bytes=int(size_bytes),
        )


def _html_escape(text: str) -> str:
    """Escape ``<``, ``>``, ``&``, ``"`` for safe inclusion in HTML attributes."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


__all__ = ["MarkdownToPdfParams", "MarkdownToPdfTool"]
