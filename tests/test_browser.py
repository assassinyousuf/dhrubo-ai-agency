import asyncio
import tempfile
from pathlib import Path

from dhrubo.tools.browser_driver import Viewport
from dhrubo.tools.null_driver import NullDriver


async def test_null_driver_navigate() -> None:
    async with NullDriver() as d:
        snap = await d.navigate("https://example.com/")
        assert snap.status_code == 200
        assert snap.final_url == "https://example.com/"


async def test_null_driver_screenshot() -> None:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "x.png"
        async with NullDriver() as d:
            await d.navigate("https://example.com/")
            shot = await d.screenshot(out, viewport=Viewport.mobile())
        assert out.exists()
        assert shot.bytes_written > 0
        assert shot.viewport_name == "mobile"


async def test_null_driver_requires_no_extra_deps() -> None:
    """NullDriver works without playwright installed."""
    d = NullDriver()
    snap = await d.navigate("https://x/")
    assert "<html" in snap.html


def test_screenshot_tool_runs_with_null(tmp_path: Path) -> None:
    from dhrubo.tools.screenshot_tool import ScreenshotTool
    from dhrubo.tools.tool_interface import ToolContext

    async def _go() -> None:
        tool = ScreenshotTool(default_output_dir=tmp_path)
        out_dir = tmp_path / "shots"
        result = await tool.safe_run(
            {"url": "https://example.com/", "driver": "null", "output_dir": str(out_dir)},
            ToolContext(requester_role="test"),
        )
        assert result.success is True
        assert result.data is not None
        shots = result.data["shots"]
        assert len(shots) >= 1  # default is desktop + mobile + tablet
        for s in shots:
            assert Path(s["path"]).exists()

    asyncio.run(_go())
