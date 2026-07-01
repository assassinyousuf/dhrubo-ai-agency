"""Permissions config: restricts which agents may use which tools."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentPermissions(BaseModel):
    """Permissions granted to a specific agent role.

    ``tools`` is an explicit allow-list, not a deny-list. An empty list
    means "no tools" — that is intentional, fail-closed is safer than
    fail-open.
    """

    role: str
    tools: list[str] = Field(default_factory=list)


class PermissionsConfig(BaseModel):
    """Top-level ``permissions.yaml`` schema."""

    agents: list[AgentPermissions] = Field(default_factory=list)

    def allows(self, role: str, tool_name: str) -> bool:
        """Return True iff ``role`` is allowed to use ``tool_name``."""
        for agent in self.agents:
            if agent.role == role:
                return tool_name in agent.tools
        return False
