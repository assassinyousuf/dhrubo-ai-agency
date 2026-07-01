"""Specialized agent implementations.

Every concrete agent inherits from :class:`dhrubo.agents.base_agent.BaseAgent`
and registers itself in the global :data:`agent_registry` at class-definition
time. Importing this subpackage imports every concrete agent, which is the
trigger that populates the registry. The CLI bootstrap relies on this.
"""

from dhrubo.agents.base_agent import (
    AgentContext,
    AgentRegistry,
    AgentResult,
    BaseAgent,
    agent_registry,
)
from dhrubo.agents.exporter import ExporterAgent
from dhrubo.agents.planner import PlannerAgent, PlannerOutput, PlanStep
from dhrubo.agents.report_writer import ReportWriterAgent
from dhrubo.agents.screenshot_agent import ScreenshotAgent
from dhrubo.agents.seo_reviewer import SeoIssue, SeoReport, SeoReviewerAgent
from dhrubo.agents.website_crawler import CrawledPage, WebsiteCrawlerAgent


def ensure_all_registered() -> list[str]:
    """Force-import all agents and return the list of registered roles.

    Idempotent: re-registering the same role just overwrites (with a warning).
    Useful as a single bootstrap call for the CLI or for tests.
    """
    # Imports above already triggered registration. This exists for
    # explicit-call clarity and to return the canonical role list.
    return agent_registry.roles()


__all__ = [
    "AgentContext",
    "AgentRegistry",
    "AgentResult",
    "BaseAgent",
    "CrawledPage",
    "ExporterAgent",
    "PlanStep",
    "PlannerAgent",
    "PlannerOutput",
    "ReportWriterAgent",
    "ScreenshotAgent",
    "SeoIssue",
    "SeoReport",
    "SeoReviewerAgent",
    "WebsiteCrawlerAgent",
    "agent_registry",
    "ensure_all_registered",
]
