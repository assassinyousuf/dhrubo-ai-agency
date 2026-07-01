"""HTTP fetch tool — the simplest possible web-access primitive.

Used by the crawler in M2 and (for non-JS sites) as a fallback to Playwright
in M3+. Honors timeouts, redirects, and ``Content-Type``.
"""

from __future__ import annotations

from typing import ClassVar

import httpx
from pydantic import BaseModel, Field

from dhrubo.core.errors import ToolError
from dhrubo.core.logger import get_logger
from dhrubo.tools.tool_interface import Tool, ToolContext, ToolParameter, ToolResult

_log = get_logger("tools.web_fetch")


class WebFetchParams(BaseModel):
    """Inputs for :class:`WebFetchTool`."""

    url: str = Field(min_length=1, max_length=2048)
    method: str = Field(default="GET", pattern=r"^(GET|HEAD)$")
    follow_redirects: bool = True
    timeout_seconds: float = Field(default=20.0, gt=0.0, le=120.0)
    max_bytes: int = Field(default=2_000_000, gt=0)  # 2MB safety cap


class WebFetchTool(Tool[WebFetchParams]):
    """Fetch a URL over HTTP and return status, headers, and body."""

    name: ClassVar[str] = "web_fetch"
    description: ClassVar[str] = "Fetch a URL via HTTP(S) and return headers + body."
    parameters: ClassVar[tuple[ToolParameter, ...]] = (
        ToolParameter("url", "string", description="Absolute URL to fetch."),
        ToolParameter("method", "GET|HEAD", required=False),
        ToolParameter("follow_redirects", "bool", required=False),
        ToolParameter("timeout_seconds", "float", required=False),
        ToolParameter("max_bytes", "int", required=False),
    )
    params_model: ClassVar[type[BaseModel]] = WebFetchParams

    async def run(self, params: WebFetchParams, ctx: ToolContext) -> ToolResult:
        _log.info(
            "tool.web_fetch.start",
            extra={
                "tool": self.name,
                "url": params.url,
                "requester": ctx.requester_role,
            },
        )
        try:
            async with httpx.AsyncClient(
                follow_redirects=params.follow_redirects,
                timeout=params.timeout_seconds,
                headers={"User-Agent": "DhruboAudit/0.1 (+https://example.local)"},
            ) as client:
                response = await client.request(params.method, params.url)
        except httpx.HTTPError as exc:
            raise ToolError(
                f"web_fetch transport error: {exc!r}",
                context={"tool": self.name, "url": params.url},
                cause=exc,
            ) from exc

        body_bytes = response.content[: params.max_bytes]
        truncated = len(response.content) > params.max_bytes
        try:
            text = body_bytes.decode(response.encoding or "utf-8", errors="replace")
        except LookupError:
            text = body_bytes.decode("utf-8", errors="replace")

        data = {
            "url": str(response.url),
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "content_type": response.headers.get("content-type", ""),
            "text": text,
            "truncated": truncated,
            "final_url": str(response.url),
        }

        if response.status_code >= 400:
            return ToolResult(
                name=self.name,
                success=False,
                data=data,
                error=f"HTTP {response.status_code}",
                metadata={"url": params.url, "status": response.status_code},
            )

        return ToolResult.ok(self.name, data=data, url=params.url, status=response.status_code)
