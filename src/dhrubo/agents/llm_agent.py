"""`LLMAgent` — declarative base class for text-only LLM agents.

Subclasses declare:

- ``system_template`` / ``user_template``: Jinja2 strings (or names of
  templates packaged under :mod:`dhrubo.prompts`)
- ``response_model``: a Pydantic ``BaseModel`` the LLM output must conform to
- ``input_keys``: keys pulled from session memory
- ``output_keys``: keys written back

The base class handles prompt rendering, the LLM call, JSON-mode request,
output validation, and a configurable retry loop.

This is the single most important abstraction in the framework — every
reviewer inherits from it.
"""

from __future__ import annotations

import json
from abc import abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from jinja2 import Environment, StrictUndefined, TemplateError
from pydantic import BaseModel, ValidationError

from dhrubo.agents.base_agent import AgentContext, AgentResult, BaseAgent
from dhrubo.core.errors import AgentError, AgentHallucinationError
from dhrubo.core.issue_id import populate_issue_ids
from dhrubo.core.logger import get_logger
from dhrubo.llm.interface import LLMMessage, LLMRequest

_log = get_logger("agents.llm")


def _prompt_env() -> Environment:
    """Jinja2 environment configured for prompts (strict undefined)."""
    return Environment(undefined=StrictUndefined, autoescape=False, trim_blocks=True)


class LLMAgent(BaseAgent):
    """Base class for any agent whose work is producing structured text via an LLM."""

    system_template: ClassVar[str] = ""
    user_template: ClassVar[str] = ""
    response_model: ClassVar[type[BaseModel] | None] = None
    use_json_mode: ClassVar[bool] = True
    max_retries: ClassVar[int] = 2  # two retries on top of the initial attempt
    # This is an intermediate scaffolding base, not an agent itself.
    __abstract_base__: ClassVar[bool] = True

    # ------------------------------------------------------------------
    # Concrete subclasses override role/input_keys/output_keys as usual.
    # ------------------------------------------------------------------

    def __init__(self, *, prompt_dir: Path | None = None) -> None:
        self._env = _prompt_env()
        self._prompt_dir = prompt_dir

    # --- prompt rendering -----------------------------------------------

    def _render(self, template: str, variables: dict[str, Any]) -> str:
        try:
            return self._env.from_string(template).render(**variables)
        except TemplateError as exc:
            raise AgentError(
                "Prompt rendering failed",
                context={"role": self.role, "template": template[:60] + "..."},
                cause=exc,
            ) from exc

    def _load_named(self, name: str) -> str:
        """Load a template by name from the package's :mod:`dhrubo.prompts` dir."""
        # Resolve the prompts/ directory alongside the installed package.
        # This works both in editable and wheel installs because we ship
        # the directory via wheel force-include.
        from dhrubo import __file__ as pkg_init  # local import to avoid cycles

        pkg_root = Path(pkg_init).parent
        candidate = pkg_root / "prompts" / name
        if not candidate.exists():
            raise AgentError(
                f"Prompt template '{name}' not found",
                context={"expected_path": str(candidate)},
            )
        return candidate.read_text(encoding="utf-8")

    # --- the LLM call ---------------------------------------------------

    async def _call_llm(
        self,
        ctx: AgentContext,
        *,
        system: str,
        user: str,
    ) -> str:
        if ctx.llm is None:
            raise AgentError(
                f"Agent '{ctx.role}' has no LLM provider configured",
                context={"role": ctx.role},
            )
        request = LLMRequest(
            model=self._resolve_model(ctx),
            messages=[
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=user),
            ],
            response_format_json=self.use_json_mode and self.response_model is not None,
        )
        completion = await ctx.llm.complete(request)
        return str(completion.content)

    def _resolve_model(self, ctx: AgentContext) -> str:
        """Pick the model. Default: read from ``ctx.metadata['model']`` or 'gpt-4o-mini'."""
        model = ctx.metadata.get("model") if isinstance(ctx.metadata, dict) else None
        return model or "gpt-4o-mini"

    # --- JSON parsing + validation --------------------------------------

    def _parse_and_validate(self, raw: str) -> BaseModel:
        if self.response_model is None:
            raise AgentError(
                f"Agent '{self.role}' has no response_model configured",
                context={"role": self.role},
            )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AgentHallucinationError(
                "LLM returned invalid JSON",
                context={"role": self.role, "snippet": raw[:200]},
                cause=exc,
            ) from exc
        try:
            return self.response_model.model_validate(payload)
        except ValidationError as exc:
            raise AgentHallucinationError(
                "LLM output failed schema validation",
                context={"role": self.role, "errors": exc.errors()[:3]},
                cause=exc,
            ) from exc

    # --- public contract -----------------------------------------------

    @abstractmethod
    def build_user_prompt(self, ctx: AgentContext) -> str:  # pragma: no cover - interface
        """Return the user message body, given the current context."""

    @abstractmethod
    def build_variables(self, ctx: AgentContext) -> dict[str, Any]:  # pragma: no cover
        """Return the template variables for system + user prompts."""

    async def execute(self, ctx: AgentContext) -> AgentResult:
        vars_ = self.build_variables(ctx)
        system = self._render(self.system_template, vars_)
        user = self._render(self.user_template, vars_)
        # Some subclasses may override the user template entirely.
        user_body = self.build_user_prompt(ctx) or user

        raw = ""
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 2):  # initial + retries
            try:
                raw = await self._call_llm(ctx, system=system, user=user_body)
                validated = self._parse_and_validate(raw)
                return self._to_result(ctx, validated)
            except AgentHallucinationError as exc:
                last_exc = exc
                _log.warning(
                    "agent.llm.retry",
                    extra={"role": self.role, "attempt": attempt, "error": str(exc)},
                )
                # Append the error to the user message so the model can correct.
                user_body = (
                    f"{user_body}\n\nYour previous response was invalid: {exc.message}\n"
                    "Return ONLY a JSON object matching the requested schema."
                )
            except AgentError:
                raise

        raise AgentHallucinationError(
            f"LLM agent '{self.role}' failed after {self.max_retries} retries",
            context={"role": self.role, "last_error": str(last_exc) if last_exc else None},
            cause=last_exc,
        )

    def _to_result(self, ctx: AgentContext, validated: BaseModel) -> AgentResult:
        """Default: serialize the validated model as JSON under ``response_key``."""
        # Back-fill stable ``id``s on every issue (M10). The LLM never
        # sees the id schema field — we compute it deterministically
        # from (severity, title, detail) so diff/runs stay stable
        # across rewords.
        payload = populate_issue_ids(validated.model_dump())
        return AgentResult.ok(self.role, response=payload)


__all__ = ["LLMAgent"]
