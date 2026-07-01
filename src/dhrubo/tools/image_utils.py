"""Image I/O helpers used by multimodal LLM providers.

Everything here is stdlib-only so the core install stays light. We do
content sniffing on magic bytes (not extension parsing) so a screenshot
named ``.bin`` is still recognised as PNG if its bytes say so.

This module lives under :mod:`dhrubo.tools` rather than :mod:`dhrubo.llm`
because it is a generic file-handling utility — the LLM boundary itself
just consumes :class:`dhrubo.llm.interface.ImageRef`.
"""

from __future__ import annotations

import base64
from pathlib import Path

from dhrubo.core.errors import ProviderError

# A real 1x1 transparent PNG (89 bytes), kept here so test fixtures don't
# have to import from null_driver just to get a valid PNG header.
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def detect_media_type(path: str | Path) -> str:
    """Return the IANA media type for ``path`` based on its magic bytes.

    Raises:
        ProviderError: if the file is missing or the format is not supported.
    """
    p = Path(path)
    try:
        head = p.read_bytes()[:12]
    except FileNotFoundError as exc:
        raise ProviderError(
            "Image file not found",
            context={"path": str(p)},
        ) from exc
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    # WEBP: 'RIFF' .... 'WEBP' (the WEBP marker sits at offset 8-12).
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    raise ProviderError(
        "Unsupported image format",
        context={"path": str(p), "head_hex": head.hex()},
    )


def read_bytes(path: str | Path) -> bytes:
    """Read raw bytes from ``path`` and surface IO errors as :class:`ProviderError`."""
    p = Path(path)
    try:
        return p.read_bytes()
    except FileNotFoundError as exc:
        raise ProviderError(
            "Image file not found",
            context={"path": str(p)},
        ) from exc


def to_data_url(path: str | Path, *, media_type: str | None = None) -> str:
    """Return ``data:<media_type>;base64,<...>`` for the given image file.

    If ``media_type`` is omitted it is inferred via :func:`detect_media_type`.
    """
    mt = media_type or detect_media_type(path)
    raw = read_bytes(path)
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mt};base64,{b64}"


__all__ = ["_PNG_1x1", "detect_media_type", "read_bytes", "to_data_url"]
