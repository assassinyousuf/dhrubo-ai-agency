"""Tests for :mod:`dhrubo.tools.markdown_to_pdf_tool`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from dhrubo.tools.markdown_to_pdf_tool import MarkdownToPdfParams, MarkdownToPdfTool
from dhrubo.tools.tool_interface import ToolContext


def _tool(monkeypatch: pytest.MonkeyPatch) -> MarkdownToPdfTool:
    """Return a tool with a known short retry policy (no real sleeps)."""
    tool = MarkdownToPdfTool()
    from dhrubo.config.models import RetryConfig

    tool._retry_policy = RetryConfig(
        max_attempts=1, initial_delay_seconds=0.001, max_delay_seconds=0.01, jitter=False
    )
    return tool


# ---------------------------------------------------------------------------
# Skip path
# ---------------------------------------------------------------------------


async def test_skips_when_weasyprint_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MarkdownToPdfTool, "is_available", staticmethod(lambda: False))
    tool = _tool(monkeypatch)

    called = {"n": 0}

    async def _blow(*_args, **_kwargs):
        called["n"] += 1
        return 0

    monkeypatch.setattr(tool, "_do_call", _blow)

    params = MarkdownToPdfParams(markdown="# Hello", output_path="/tmp/x.pdf")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="exporter"))
    assert res.success is True
    assert res.data["skipped"] is True
    assert "weasyprint" in res.data["reason"].lower()
    assert res.data["output_path"] is None
    assert called["n"] == 0  # _do_call must never run when unavailable


# ---------------------------------------------------------------------------
# Happy path — gated on `markdown` + `weasyprint` actually being present.
# ---------------------------------------------------------------------------


# The remaining "happy path" tests require both ``weasyprint`` and
# ``markdown`` to be importable on the host. In CI environments without
# the `[pdf]` extra installed they should skip rather than fail; the
# skip-path tests above already cover the no-pdf case.
_skip_if_no_pdf = pytest.mark.skipif(
    not MarkdownToPdfTool.is_available(),
    reason="weasyprint / markdown not installed; skip PDF render test",
)


@_skip_if_no_pdf
async def test_renders_pdf_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MarkdownToPdfTool, "is_available", staticmethod(lambda: True))
    tool = _tool(monkeypatch)

    captured: dict[str, Any] = {}

    async def _render(*, html, base_url, output_path):
        captured["html"] = html
        captured["base_url"] = base_url
        captured["output_path"] = output_path
        # Write a stable stub so we can assert size_bytes > 0.
        Path(output_path).write_bytes(b"%PDF-stub-1.4\n")
        return Path(output_path).stat().st_size

    monkeypatch.setattr(tool, "_do_call", _render)

    params = MarkdownToPdfParams(
        markdown="# Hello\n\nA paragraph with **bold**.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n",
        output_path="/tmp/m6-sample.pdf",
        page_size="a4",
        title="Audit — example.com",
    )
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="exporter"))
    assert res.success is True
    data = res.data
    assert data["skipped"] is False
    assert data["page_size"] == "a4"
    assert data["size_bytes"] > 0
    # CSS page size encoded in the HTML.
    assert "A4" in captured["html"]
    # Title is HTML-escaped (em-dash becomes &#8212;).
    assert "Audit &#8212; example.com" in captured["html"]
    # Markdown table cells made it into <td> tags via the markdown lib.
    assert "<td>1</td>" in captured["html"]


@_skip_if_no_pdf
async def test_propagates_base_url_and_page_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MarkdownToPdfTool, "is_available", staticmethod(lambda: True))
    tool = _tool(monkeypatch)
    captured: dict[str, Any] = {}

    async def _capture(*, html, base_url, output_path):
        captured["base_url"] = base_url
        captured["output_path"] = output_path
        Path(output_path).write_bytes(b"%PDF-stub")
        return 10

    monkeypatch.setattr(tool, "_do_call", _capture)

    params = MarkdownToPdfParams(
        markdown="content",
        output_path="/tmp/page.pdf",
        base_url="file:///var/audit/run/",
        page_size="letter",
    )
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="exporter"))
    assert res.success is True
    assert res.data["page_size"] == "letter"
    assert captured["base_url"] == "file:///var/audit/run/"
    assert captured["output_path"] == "/tmp/page.pdf"
    # CSS page size.
    assert "LETTER" in captured["html"]


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------


@_skip_if_no_pdf
async def test_handles_renderer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MarkdownToPdfTool, "is_available", staticmethod(lambda: True))
    tool = _tool(monkeypatch)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("weasyprint blew up")

    monkeypatch.setattr(tool, "_do_call", _boom)

    params = MarkdownToPdfParams(markdown="# Hello", output_path="/tmp/x.pdf")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="exporter"))
    assert res.success is False
    assert "rendering failed" in (res.error or "").lower()


# ---------------------------------------------------------------------------
# Graceful degradation when only WeasyPrint (or only markdown) is missing
# ---------------------------------------------------------------------------


async def test_skips_when_only_markdown_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even when ``is_available`` reports True, a missing ``markdown``
    package at render time must produce a skip-payload, not a hard fail.
    """
    monkeypatch.setattr(MarkdownToPdfTool, "is_available", staticmethod(lambda: True))
    tool = _tool(monkeypatch)

    called = {"n": 0}

    async def _blow(*_args, **_kwargs):
        called["n"] += 1
        return 0

    monkeypatch.setattr(tool, "_do_call", _blow)

    # Hide the markdown package from the import system for the duration.
    import builtins

    real_import = builtins.__import__

    def _no_markdown(name, *args, **kwargs):
        if name == "markdown" or name.startswith("markdown."):
            raise ImportError("markdown hidden by test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_markdown)

    params = MarkdownToPdfParams(markdown="# Hi", output_path="/tmp/x.pdf")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="exporter"))
    assert res.success is True
    assert res.data["skipped"] is True
    assert "markdown" in res.data["reason"].lower()
    assert called["n"] == 0  # _do_call must not be reached


# ---------------------------------------------------------------------------
# Param validation
# ---------------------------------------------------------------------------


async def test_rejects_bad_params(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)
    res = await tool.safe_run(
        {"markdown": "", "output_path": "/tmp/x.pdf"},
        ToolContext(requester_role="exporter"),
    )
    assert res.success is False
    assert "Invalid params" in (res.error or "")


async def test_rejects_bad_page_size(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)
    res = await tool.safe_run(
        {"markdown": "hi", "output_path": "/tmp/x.pdf", "page_size": "tabloid"},
        ToolContext(requester_role="exporter"),
    )
    assert res.success is False
    assert "page_size" in (res.error or "")


# ---------------------------------------------------------------------------
# is_available helper
# ---------------------------------------------------------------------------


def test_is_available_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default state — weasyprint may or may not be installed in the test env,
    # we don't assert on it; we just verify the helper returns a bool without
    # raising (it shields ImportError into False).
    assert isinstance(MarkdownToPdfTool.is_available(), bool)
