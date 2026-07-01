"""`ScreenshotAgent` — captures screenshots via :class:`ScreenshotTool`.

M3 ships the null-driver path so it works without Chromium; switching to
Playwright is a config flip.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from dhrubo.agents.base_agent import AgentContext, AgentResult, BaseAgent
from dhrubo.core.logger import get_logger
from dhrubo.tools.screenshot_tool import ScreenshotTool
from dhrubo.tools.tool_interface import ToolContext

_log = get_logger("agents.screenshot")


class ScreenshotAgent(BaseAgent):
    role: ClassVar[str] = "screenshot_agent"
    input_keys: ClassVar[tuple[str, ...]] = ("target_url",)
    output_keys: ClassVar[tuple[str, ...]] = ("screenshot_paths",)
    required_tools: ClassVar[tuple[str, ...]] = ("screenshot",)

    def __init__(self, *, output_root: Path | None = None) -> None:
        self._tool = ScreenshotTool(default_output_dir=output_root)

    async def execute(self, ctx: AgentContext) -> AgentResult:
        url = ctx.inputs.get("target_url")
        if not url:
            return AgentResult.fail(self.role, error="missing target_url")

        # Auto-promote to Playwright when the env var is set AND the
        # browser extra is installed. Falls back to null otherwise.
        driver_name = "null"
        if os.environ.get("DHRUBO_USE_REAL_BROWSER", "").lower() in ("1", "true", "yes"):
            try:
                from dhrubo.tools.null_driver import _DRIVERS

                if "playwright" in _DRIVERS:
                    driver_name = "playwright"
            except Exception:  # pragma: no cover - best-effort detection
                pass

        tool_ctx = ToolContext(requester_role=self.role)
        result = await self._tool.safe_run(
            {
                "url": str(url),
                "driver": driver_name,
                "output_dir": str(self._tool._default_output_dir),
            },
            tool_ctx,
        )
        if not result.success or result.data is None:
            # Retry once with null driver if Playwright failed.
            if driver_name == "playwright":
                _log.warning("screenshot.playwright_failed_fallback_null")
                fb = await self._tool.safe_run(
                    {"url": str(url), "driver": "null"},
                    tool_ctx,
                )
                if fb.success and fb.data is not None:
                    return AgentResult.ok(self.role, screenshot_paths=fb.data["shots"], **fb.data)
            return AgentResult.fail(
                self.role,
                error=result.error or "screenshot tool failed",
                **{k: v for k, v in (result.metadata or {}).items()},
            )

        data = result.data
        return AgentResult.ok(
            self.role,
            screenshot_paths=data["shots"],
            final_url=data.get("final_url", str(url)),
            title=data.get("title", ""),
        )
