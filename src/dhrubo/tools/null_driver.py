"""A no-op :class:`BrowserDriver` for unit tests and offline environments.

Returns deterministic PNG bytes (a 1x1 black PNG) so downstream code that
inspects file size / format doesn't break.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from dhrubo.tools.browser_driver import (
    BrowserDriver,
    PageSnapshot,
    Screenshot,
    Viewport,
)

# 1x1 transparent PNG (89 bytes).
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


class NullDriver(BrowserDriver):
    """A driver that pretends to navigate and produce empty screenshots.

    Useful when:
    - running unit tests without a browser,
    - users explicitly opt out of browser rendering,
    - the browser extra isn't installed.
    """

    name = "null"

    def __init__(self, *, html: str = "<html><head><title>NoDriver</title></head><body></body></html>") -> None:
        self._html = html
        self._current_url: str | None = None

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def navigate(
        self,
        url: str,
        *,
        wait_until: str = "networkidle",
        timeout_seconds: float = 30.0,
    ) -> PageSnapshot:
        self._current_url = url
        return PageSnapshot(
            url=url,
            final_url=url,
            status_code=200,
            title="NoDriver",
            html=self._html,
        )

    async def screenshot(
        self,
        path: Path,
        *,
        viewport: Viewport | None = None,
        full_page: bool = True,
    ) -> Screenshot:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_PNG_1x1)
        vp = viewport or Viewport.desktop()
        return Screenshot(
            path=path,
            viewport_name=vp.name,
            width=vp.width,
            height=vp.height,
            bytes_written=len(_PNG_1x1),
            metadata={"driver": "null", "full_page": full_page},
        )


# A driver registry so tools can resolve by name.
_DRIVERS: dict[str, type[BrowserDriver]] = {}


def register_driver(name: str, cls: type[BrowserDriver]) -> None:
    _DRIVERS[name] = cls


def get_driver(name: str, **kwargs: Any) -> BrowserDriver:
    try:
        cls = _DRIVERS[name]
    except KeyError as exc:
        raise KeyError(
            f"Unknown browser driver '{name}'. Known: {sorted(_DRIVERS)}"
        ) from exc
    return cls(**kwargs)


# Built-ins.
register_driver("null", NullDriver)


def _register_playwright_if_available() -> None:
    """Lazy-register the Playwright driver if installed.

    Importing playwright is expensive (and requires Chromium download).
    We only load the driver when a user explicitly asks for it.
    """
    try:
        from dhrubo.tools.playwright_impl import PlaywrightDriver
    except ImportError:
        return
    register_driver("playwright", PlaywrightDriver)


_register_playwright_if_available()
