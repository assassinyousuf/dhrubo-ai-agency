"""Tool implementations.

Tools are the abstraction layer over external dependencies (Playwright,
Lighthouse, etc.). Agents must never import a vendor library directly;
they go through the :class:`dhrubo.tools.tool_interface.Tool` interface.
"""

from dhrubo.tools.tool_interface import (
    Tool,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
    tool_registry,
)

__all__ = [
    "Tool",
    "ToolContext",
    "ToolParameter",
    "ToolRegistry",
    "ToolResult",
    "tool_registry",
]
