"""Tool abstract interface and registry.

A tool is a typed capability: it has a ``name``, declares its parameters
via Pydantic, performs one well-defined side-effect, and returns a
:class:`ToolResult`.

Agents never call vendor libraries (Playwright, requests, ...) directly.
They invoke a tool whose implementation is free to be swapped.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, ValidationError

from dhrubo.core.errors import ToolError
from dhrubo.core.logger import get_logger

_log = get_logger("tools")

TParams = TypeVar("TParams", bound=BaseModel)


@dataclass(slots=True)
class ToolContext:
    """Runtime context handed to a tool.

    Kept intentionally small: agents will pass only what the tool needs.
    """

    requester_role: str
    metadata: dict[str, Any] = field(default_factory=dict)
    tracer: Any | None = None


@dataclass(slots=True)
class ToolResult:
    """The outcome of a tool invocation."""

    name: str
    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, name: str, data: Any, **metadata: Any) -> ToolResult:
        return cls(name=name, success=True, data=data, metadata=dict(metadata))

    @classmethod
    def fail(cls, name: str, error: str, **metadata: Any) -> ToolResult:
        return cls(name=name, success=False, error=error, metadata=dict(metadata))


@dataclass(slots=True, frozen=True)
class ToolParameter:
    """Lightweight descriptor for a tool's inputs (for introspection / docs)."""

    name: str
    type: str
    required: bool = True
    description: str = ""


class Tool[TParams: BaseModel](ABC):
    """Abstract base class for tools.

    Subclasses set:

    - ``name``: stable identifier
    - ``description``: human-readable purpose
    - ``parameters``: ``ToolParameter`` tuple for introspection
    - ``params_model``: a Pydantic BaseModel subclass used for validation
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    parameters: ClassVar[tuple[ToolParameter, ...]] = ()
    params_model: ClassVar[type[BaseModel] | None] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "__abstract__", False):
            return
        if cls.name:
            tool_registry.register(cls)
        elif cls is not Tool:
            _log.warning(
                "tool.no_name",
                extra={"class": f"{cls.__module__}.{cls.__name__}"},
            )

    @abstractmethod
    async def run(self, params: TParams, ctx: ToolContext) -> ToolResult:
        """Execute the tool's side-effect and return a result."""

    def validate_params(self, raw: dict[str, Any]) -> TParams:
        """Validate raw input dict against :attr:`params_model`."""
        if self.params_model is None:
            raise ToolError(
                f"Tool '{self.name}' has no params_model declared",
                context={"tool": self.name},
            )
        try:
            validated = self.params_model.model_validate(raw)
            return validated  # type: ignore[return-value]
        except ValidationError as exc:
            raise ToolError(
                f"Invalid params for tool '{self.name}'",
                context={"tool": self.name, "errors": exc.errors()},
                cause=exc,
            ) from exc

    async def safe_run(self, raw_params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Validate-then-run wrapper that catches errors and returns a failed :class:`ToolResult`."""
        try:
            params = self.validate_params(raw_params)
        except ToolError as exc:
            return ToolResult.fail(self.name, error=str(exc), **exc.context)
        try:
            return await self.run(params, ctx)
        except Exception as exc:
            _log.exception(
                "tool.failed",
                extra={"tool": self.name, "requester": ctx.requester_role},
            )
            return ToolResult.fail(self.name, error=f"unexpected: {exc!r}")


class ToolRegistry:
    """In-memory registry mapping tool names to :class:`Tool` subclasses."""

    def __init__(self) -> None:
        self._registry: dict[str, type[Tool[Any]]] = {}

    def register(self, tool_cls: type[Tool[Any]]) -> None:
        if not issubclass(tool_cls, Tool):
            raise TypeError(f"{tool_cls!r} is not a Tool subclass")
        if not tool_cls.name:
            raise ValueError(f"{tool_cls!r} has no name defined")
        self._registry[tool_cls.name] = tool_cls

    def get(self, name: str) -> type[Tool[Any]]:
        try:
            return self._registry[name]
        except KeyError as exc:
            raise ToolError(
                f"Unknown tool '{name}'",
                context={"tool": name, "known": sorted(self._registry)},
            ) from exc

    def instantiate(self, name: str, **kwargs: Any) -> Tool[Any]:
        return self.get(name)(**kwargs)

    def names(self) -> list[str]:
        return sorted(self._registry)


tool_registry = ToolRegistry()


__all__ = [
    "Tool",
    "ToolContext",
    "ToolParameter",
    "ToolRegistry",
    "ToolResult",
    "tool_registry",
]
