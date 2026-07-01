"""`ScreenshotTool` — captures full-page screenshots across viewports.

The tool is stateful in the sense that it owns a :class:`BrowserDriver`;
agents that need multiple shots of the same page call it once with a list
of viewports and it drives the browser through them.

When the ``[browser]`` extra isn't installed, the tool defaults to the
:class:`NullDriver` so the pipeline can still run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from dhrubo.core.errors import ToolError
from dhrubo.core.logger import get_logger
from dhrubo.tools.browser_driver import (
    DEFAULT_VIEWPORTS,
    BrowserDriver,
    Viewport,
)
from dhrubo.tools.tool_interface import Tool, ToolContext, ToolParameter, ToolResult

_log = get_logger("tools.screenshot")


class ViewportSpec(BaseModel):
    """One viewport to capture."""

    name: str
    width: int = Field(gt=0, le=4096)
    height: int = Field(gt=0, le=4096)
    full_page: bool = True


class ScreenshotParams(BaseModel):
    """Inputs for :class:`ScreenshotTool`."""

    url: str = Field(min_length=1, max_length=2048)
    output_dir: str = Field(default="", description="Where to write PNGs. Empty = tool default.")
    viewports: list[ViewportSpec] = Field(default_factory=list)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=120.0)
    driver: str = Field(default="null", description='"null" or "playwright"')


class ScreenshotTool(Tool[ScreenshotParams]):
    """Captures page screenshots across one or more viewports."""

    name: ClassVar[str] = "screenshot"
    description: ClassVar[str] = "Navigate a headless browser and capture screenshots."
    parameters: ClassVar[tuple[ToolParameter, ...]] = (
        ToolParameter("url", "string"),
        ToolParameter("output_dir", "string", required=False),
        ToolParameter("viewports", "ViewportSpec[]", required=False),
        ToolParameter("timeout_seconds", "float", required=False),
        ToolParameter("driver", '"null"|"playwright"', required=False),
    )
    params_model: ClassVar[type[BaseModel]] = ScreenshotParams

    def __init__(self, default_output_dir: Path | None = None) -> None:
        self._default_output_dir = default_output_dir or Path("./screenshots")

    @staticmethod
    def _viewport_from(spec: ViewportSpec) -> Viewport:
        # Auto-derive the mobile flag based on size.
        is_mobile = spec.width <= 480
        return Viewport(
            name=spec.name,
            width=spec.width,
            height=spec.height,
            is_mobile=is_mobile,
        )

    def _resolve_driver(self, name: str) -> BrowserDriver:
        from dhrubo.tools.null_driver import get_driver

        return get_driver(name)

    async def run(self, params: ScreenshotParams, ctx: ToolContext) -> ToolResult:
        output_dir = Path(params.output_dir) if params.output_dir else self._default_output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        if params.viewports:
            viewports = [self._viewport_from(v) for v in params.viewports]
        else:
            viewports = list(DEFAULT_VIEWPORTS)

        driver = self._resolve_driver(params.driver)
        paths: list[dict[str, Any]] = []
        try:
            await driver.start()
            snap = await driver.navigate(
                params.url, timeout_seconds=params.timeout_seconds
            )
            for vp in viewports:
                safe_name = "".join(
                    c if c.isalnum() or c in ("-", "_", ".") else "_" for c in params.url
                )[:80] or "page"
                out = output_dir / f"{safe_name}_{vp.name}.png"
                shot = await driver.screenshot(out, viewport=vp, full_page=True)
                paths.append(
                    {
                        "viewport": vp.name,
                        "width": shot.width,
                        "height": shot.height,
                        "path": str(shot.path),
                        "bytes": shot.bytes_written,
                    }
                )
            _log.info(
                "screenshot.complete",
                extra={
                    "tool": self.name,
                    "url": params.url,
                    "shots": len(paths),
                    "driver": params.driver,
                    "requester": ctx.requester_role,
                },
            )
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                f"screenshot tool failed: {exc!r}",
                context={"tool": self.name, "url": params.url, "driver": params.driver},
                cause=exc,
            ) from exc
        finally:
            await driver.close()

        return ToolResult.ok(
            self.name,
            data={
                "title": getattr(snap, "title", ""),
                "final_url": getattr(snap, "final_url", params.url),
                "status_code": getattr(snap, "status_code", 0),
                "driver": params.driver,
                "shots": paths,
            },
            url=params.url,
            shot_count=len(paths),
        )
