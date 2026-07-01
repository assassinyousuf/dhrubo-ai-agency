"""Abstract browser-driver interface.

Agents must not import Playwright / Selenium / etc. directly. They use one
of these drivers through :class:`dhrubo.tools.screenshot_tool.ScreenshotTool`.

The interface supports a one-shot ``session()`` async context manager that
opens a single browser/context and reuses it across calls — important for
performance when capturing multiple viewports of the same page.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class ViewportKind(StrEnum):
    DESKTOP = "desktop"
    MOBILE = "mobile"
    TABLET = "tablet"


@dataclass(slots=True, frozen=True)
class Viewport:
    """A viewport configuration."""

    name: str
    width: int
    height: int
    device_scale_factor: float = 1.0
    is_mobile: bool = False

    @classmethod
    def desktop(cls) -> Viewport:
        return cls(name="desktop", width=1440, height=900, device_scale_factor=1.0)

    @classmethod
    def mobile(cls) -> Viewport:
        return cls(name="mobile", width=390, height=844, device_scale_factor=2.0, is_mobile=True)

    @classmethod
    def tablet(cls) -> Viewport:
        return cls(name="tablet", width=1024, height=1366, device_scale_factor=2.0, is_mobile=True)


# Default viewport presets for the screenshot pipeline.
DEFAULT_VIEWPORTS: tuple[Viewport, ...] = (
    Viewport.desktop(),
    Viewport.mobile(),
    Viewport.tablet(),
)


@dataclass(slots=True)
class Screenshot:
    """A captured screenshot."""

    path: Path
    viewport_name: str
    width: int
    height: int
    bytes_written: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PageSnapshot:
    """The minimal DOM/page context returned after navigation."""

    url: str
    final_url: str
    status_code: int
    title: str
    html: str
    cookies: list[dict[str, Any]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)


class BrowserDriver(ABC):
    """Contract every concrete browser driver must implement.

    Concrete drivers (Playwright, Selenium, etc.) wrap vendor APIs in
    this surface. Tools (e.g. ScreenshotTool) consume this surface only.
    """

    name: str = "abstract"

    @abstractmethod
    async def start(self) -> None:
        """Boot the driver (open browser process, etc.)."""

    @abstractmethod
    async def close(self) -> None:
        """Tear down the driver."""

    @abstractmethod
    async def navigate(
        self,
        url: str,
        *,
        wait_until: str = "networkidle",
        timeout_seconds: float = 30.0,
    ) -> PageSnapshot:
        """Navigate to ``url`` and return the resulting page snapshot."""

    @abstractmethod
    async def screenshot(
        self,
        path: Path,
        *,
        viewport: Viewport | None = None,
        full_page: bool = True,
    ) -> Screenshot:
        """Capture a screenshot of the current page to ``path``."""

    async def __aenter__(self) -> BrowserDriver:
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
