"""Runtime settings, exposed via Pydantic Settings.

Environment variables override YAML defaults. The ``DHRUBO_`` prefix
keeps us out of the global env namespace.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LoggingSettings(BaseModel):
    level: str = "INFO"
    json_logs: bool = True


class OutputSettings(BaseModel):
    directory: Path = Path("./output")
    retain_runs: int = Field(default=10, ge=0)


class LLMSettings(BaseModel):
    default_provider: str = "openai"
    api_key_env: str = "OPENAI_API_KEY"


class ExportSettings(BaseModel):
    """PDF export options (M6+)."""

    pdf_enabled: bool = True
    pdf_format: Literal["a4", "letter"] = "a4"


class GitHubSettings(BaseModel):
    """GitHub integration options (M12+).

    ``api_key_env`` and ``repository_env`` store the *names* of
    the environment variables holding the secret and the default
    repository. The CLI reads them with ``os.environ.get(...)``.
    Never put the secret itself in this config.
    """

    api_key_env: str = "GITHUB_TOKEN"
    repository_env: str = "GITHUB_REPOSITORY"
    api_base_url: str = "https://api.github.com"


class Settings(BaseSettings):
    """Root runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="DHRUBO_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    project_name: str = "dhrubo-ai-agency"
    environment: str = Field(default="development")
    config_directory: Path = Path("./config")

    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
    github: GitHubSettings = Field(default_factory=GitHubSettings)
