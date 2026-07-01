"""Runtime settings, exposed via Pydantic Settings.

Environment variables override YAML defaults. The ``DHRUBO_`` prefix
keeps us out of the global env namespace.
"""

from __future__ import annotations

from pathlib import Path

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
