"""Abstract base class for all agents.

An agent in Dhrubo is a small, specialized piece of code with:

- a stable ``role`` string used for routing and permissions,
- declared ``input_keys`` and ``output_keys`` describing what it reads
  from / writes to session memory,
- an async ``execute()`` that does the actual work,
- registered metadata so the workflow engine can schedule it.

Each agent owns its own prompt template(s) — there are no shared mega-prompts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from dhrubo.core.errors import AgentError
from dhrubo.core.logger import get_logger

_log = get_logger("agents")


@dataclass(slots=True)
class AgentContext:
    """The bag of state an agent operates on.

    The session memory is passed by reference (read/write). Tools and the
    LLM provider are injected by the workflow engine so agents remain
    composable and testable.
    """

    role: str
    inputs: dict[str, Any] = field(default_factory=dict)
    session_memory: Any | None = None
    llm: Any | None = None
    tracer: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentResult:
    """The structured output of an agent's execution."""

    role: str
    outputs: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, role: str, **outputs: Any) -> AgentResult:
        return cls(role=role, outputs=outputs, success=True)

    @classmethod
    def fail(cls, role: str, error: str, **metadata: Any) -> AgentResult:
        return cls(role=role, success=False, error=error, metadata=dict(metadata))


class BaseAgent(ABC):
    """Contract every agent must satisfy.

    Subclasses must declare:

    - ``role``: stable identifier, e.g. ``"ui_reviewer"``
    - ``input_keys``: keys the agent reads from ``AgentContext.inputs``
    - ``output_keys``: keys the agent writes into ``AgentResult.outputs``
    - ``required_tools``: tool names the agent expects (validated against
      :class:`dhrubo.config.permissions.PermissionsConfig`)
    """

    role: ClassVar[str] = ""
    input_keys: ClassVar[tuple[str, ...]] = ()
    output_keys: ClassVar[tuple[str, ...]] = ()
    required_tools: ClassVar[tuple[str, ...]] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Intermediate scaffolding bases opt out of agent registration by
        # setting ``__abstract_base__ = True`` *in their own __dict__*.
        # (We can't just read it off the class — it inherits upward.)
        if cls.__dict__.get("__abstract_base__", False):
            return
        if cls.role:
            agent_registry.register(cls)
        elif cls is not BaseAgent:
            _log.warning(
                "agent.no_role",
                extra={"class": f"{cls.__module__}.{cls.__name__}"},
            )

    @abstractmethod
    async def execute(self, ctx: AgentContext) -> AgentResult:
        """Run the agent's logic.

        Implementations should:

        - read declared inputs from ``ctx.inputs``,
        - write declared outputs into the returned :class:`AgentResult`,
        - raise :class:`AgentError` subclasses for known failure modes.
        """

    async def safe_execute(self, ctx: AgentContext) -> AgentResult:
        """Run ``execute`` and capture any exception into a failed result.

        The workflow engine prefers this entry point so one failing agent
        does not abort the entire pipeline.
        """
        try:
            return await self.execute(ctx)
        except AgentError as exc:
            _log.warning(
                "agent.failed",
                extra={"role": ctx.role, "error": str(exc), "context": exc.context},
            )
            return AgentResult.fail(ctx.role, error=str(exc), **exc.context)
        except Exception as exc:
            _log.exception(
                "agent.unexpected_error",
                extra={"role": ctx.role},
            )
            return AgentResult.fail(ctx.role, error=f"unexpected: {exc!r}")


class AgentRegistry:
    """In-memory registry mapping ``role`` strings to :class:`BaseAgent` subclasses."""

    def __init__(self) -> None:
        self._registry: dict[str, type[BaseAgent]] = {}

    def register(self, agent_cls: type[BaseAgent]) -> None:
        if not issubclass(agent_cls, BaseAgent):
            raise TypeError(f"{agent_cls!r} is not a BaseAgent subclass")
        if not agent_cls.role:
            raise ValueError(f"{agent_cls!r} has no role defined")
        if agent_cls.role in self._registry:
            existing = self._registry[agent_cls.role]
            if existing is not agent_cls:
                _log.warning(
                    "agent.role_replaced",
                    extra={"role": agent_cls.role, "old": existing.__name__, "new": agent_cls.__name__},
                )
        self._registry[agent_cls.role] = agent_cls

    def get(self, role: str) -> type[BaseAgent]:
        try:
            return self._registry[role]
        except KeyError as exc:
            raise AgentError(
                f"No agent registered for role '{role}'",
                context={"role": role, "known": sorted(self._registry)},
            ) from exc

    def instantiate(self, role: str, **kwargs: Any) -> BaseAgent:
        return self.get(role)(**kwargs)

    def roles(self) -> list[str]:
        return sorted(self._registry)


agent_registry = AgentRegistry()


__all__ = ["AgentContext", "AgentRegistry", "AgentResult", "BaseAgent", "agent_registry"]
