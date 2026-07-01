"""Playwright-backed :class:`BrowserDriver`.

This module imports Playwright at module load. It is only imported by the
driver registry if ``playwright`` is installed (``pip install dhrubo-ai-agency[browser]``).
Install Chromium once via ``playwright install chromium``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from dhrubo.core.errors import ToolError
from dhrubo.core.logger import get_logger
from dhrubo.tools.browser_driver import (
    BrowserDriver,
    PageSnapshot,
    Screenshot,
    Viewport,
)

_log = get_logger("tools.playwright")


class PlaywrightDriver(BrowserDriver):
    """A browser driver backed by Playwright.

    Constructor:
        headless: launch in headless mode (default True).
        channel: optional browser channel ("chrome", "msedge", ...). If
            None, uses the bundled Chromium.
        user_agent: optional UA override.
        proxy: optional proxy dict passed to Playwright.

    Lifecycle:
        Use ``async with PlaywrightDriver() as driver:`` — it will start
        and stop the browser around its use. For long-running workflows
        you can also ``await driver.start()`` / ``await driver.close()``
        manually.
    """

    name = "playwright"

    def __init__(
        self,
        *,
        headless: bool = True,
        channel: str | None = None,
        user_agent: str | None = None,
        proxy: dict[str, str] | None = None,
    ) -> None:
        self._headless = headless
        self._channel = channel
        self._user_agent = user_agent
        self._proxy = proxy
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._current_url: str | None = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {"headless": self._headless}
        if self._channel:
            launch_kwargs["channel"] = self._channel
        if self._proxy:
            launch_kwargs["proxy"] = self._proxy
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)

        context_kwargs: dict[str, Any] = {}
        if self._user_agent:
            context_kwargs["user_agent"] = self._user_agent
        self._ctx = await self._browser.new_context(**context_kwargs)
        _log.info(
            "playwright.start",
            extra={"driver": self.name, "headless": self._headless, "channel": self._channel},
        )

    async def close(self) -> None:
        try:
            if self._ctx is not None:
                await self._ctx.close()
        except Exception:  # pragma: no cover - best-effort shutdown
            _log.exception("playwright.close_context_failed")
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception:  # pragma: no cover
            _log.exception("playwright.close_browser_failed")
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:  # pragma: no cover
            _log.exception("playwright.stop_failed")
        self._ctx = None
        self._browser = None
        self._playwright = None

    def _require_context(self) -> BrowserContext:
        if self._ctx is None:
            raise ToolError(
                "PlaywrightDriver not started — call start() first or use 'async with'.",
                context={"driver": self.name},
            )
        return self._ctx

    async def navigate(
        self,
        url: str,
        *,
        wait_until: str = "networkidle",
        timeout_seconds: float = 30.0,
    ) -> PageSnapshot:
        ctx = self._require_context()
        page = await ctx.new_page()
        try:
            try:
                response = await page.goto(url, wait_until=wait_until, timeout=timeout_seconds * 1000)  # type: ignore[arg-type]
            except PlaywrightTimeoutError as exc:
                raise ToolError(
                    f"Playwright navigation timed out for {url}",
                    context={"url": url, "timeout_s": timeout_seconds},
                    cause=exc,
                ) from exc
            status = response.status if response is not None else 0
            html = await page.content()
            title = await page.title()
            cookies = [dict(c) for c in await ctx.cookies()]
            headers = dict(response.headers) if response is not None else {}
            final_url = page.url
        finally:
            await page.close()

        self._current_url = final_url
        return PageSnapshot(
            url=url,
            final_url=final_url,
            status_code=status,
            title=title,
            html=html,
            cookies=cookies,
            headers=headers,
        )

    async def screenshot(
        self,
        path: Path,
        *,
        viewport: Viewport | None = None,
        full_page: bool = True,
    ) -> Screenshot:
        ctx = self._require_context()
        vp = viewport or Viewport.desktop()
        page = await ctx.new_page()
        try:
            await page.set_viewport_size({"width": vp.width, "height": vp.height})
            if self._current_url is None:
                raise ToolError(
                    "screenshot() called before navigate()",
                    context={"driver": self.name},
                )
            # Re-navigate to ensure the new viewport re-renders.
            await page.goto(self._current_url, wait_until="networkidle")
            path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(path), full_page=full_page)
            size = path.stat().st_size
        finally:
            await page.close()
        return Screenshot(
            path=path,
            viewport_name=vp.name,
            width=vp.width,
            height=vp.height,
            bytes_written=size,
            metadata={"driver": self.name, "full_page": full_page},
        )
