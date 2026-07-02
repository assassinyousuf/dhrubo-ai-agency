"""Global Browser Pool for Playwright.

Manages a single global Chromium Browser instance and provides a pool of 
BrowserContexts to avoid memory exhaustion during highly concurrent batch runs.
"""

from __future__ import annotations

import asyncio
from typing import Any

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from dhrubo.core.logger import get_logger

_log = get_logger("tools.browser_pool")


class BrowserPool:
    """Singleton pool for BrowserContexts."""

    _instance: BrowserPool | None = None
    
    def __init__(self, max_size: int = 10, headless: bool = True):
        self._max_size = max_size
        self._headless = headless
        
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: asyncio.Queue[BrowserContext] = asyncio.Queue(maxsize=max_size)
        self._active = 0
        self._lock = asyncio.Lock()
        
    @classmethod
    def get_instance(cls, max_size: int = 10) -> BrowserPool:
        if cls._instance is None:
            cls._instance = cls(max_size=max_size)
        return cls._instance

    async def _init_browser(self) -> None:
        if self._playwright is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless,
                args=["--disable-dev-shm-usage", "--no-sandbox"]
            )
            _log.info("browser_pool.initialized", extra={"max_size": self._max_size})

    async def acquire(self) -> BrowserContext:
        """Get a context from the pool. Blocks if at max capacity."""
        async with self._lock:
            if self._playwright is None:
                await self._init_browser()
                
            # If we haven't reached max size and queue is empty, create a new one
            if self._contexts.empty() and self._active < self._max_size:
                assert self._browser is not None
                ctx = await self._browser.new_context()
                self._active += 1
                return ctx

        # Otherwise, wait for one to be returned to the queue
        ctx = await self._contexts.get()
        return ctx

    async def release(self, ctx: BrowserContext) -> None:
        """Return a context to the pool. Clears cookies/state."""
        try:
            await ctx.clear_cookies()
        except Exception as e:
            _log.warning("browser_pool.release_error", extra={"error": str(e)})
        
        await self._contexts.put(ctx)

    async def close_all(self) -> None:
        """Shutdown the pool and close the browser."""
        async with self._lock:
            while not self._contexts.empty():
                ctx = self._contexts.get_nowait()
                await ctx.close()
            
            if self._browser:
                await self._browser.close()
                self._browser = None
                
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            
            self._active = 0
            _log.info("browser_pool.closed")

