"""Application configuration.

All settings are overridable through environment variables. A single
``Settings`` instance is created at import time and exposed as ``settings``.
Secrets are stored here but are NEVER sent to prompts or logs (see the
observability layer for redaction).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration for the CI Triage Agent."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ----- LLM -----
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    model: str = Field(default="gpt-4o", alias="MODEL")
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    llm_timeout_seconds: float = Field(default=90.0, alias="LLM_TIMEOUT_SECONDS")
    max_tokens: int = Field(default=2048, alias="MAX_TOKENS")

    # ----- Budgets / guardrails -----
    max_iterations: int = Field(default=20, alias="MAX_ITERATIONS")
    max_tool_calls: int = Field(default=50, alias="MAX_TOOL_CALLS")
    max_execution_seconds: int = Field(default=900, alias="MAX_EXECUTION_SECONDS")
    max_tokens_total: int = Field(default=200_000, alias="MAX_TOKENS_TOTAL")
    est_cost_per_1k_in: float = Field(default=0.005, alias="EST_COST_PER_1K_IN")
    est_cost_per_1k_out: float = Field(default=0.015, alias="EST_COST_PER_1K_OUT")

    # ----- Sandbox -----
    sandbox_backend: str = Field(default="docker", alias="SANDBOX_BACKEND")
    sandbox_image: str = Field(default="ci-triage-sandbox:dev", alias="SANDBOX_IMAGE")
    sandbox_cpu_limit: str = Field(default="1.0", alias="SANDBOX_CPU_LIMIT")
    sandbox_memory_limit: str = Field(default="1g", alias="SANDBOX_MEMORY_LIMIT")
    sandbox_command_timeout: float = Field(default=120.0, alias="SANDBOX_COMMAND_TIMEOUT")
    sandbox_network: bool = Field(default=False, alias="SANDBOX_NETWORK")
    sandbox_pull: bool = Field(default=True, alias="SANDBOX_PULL")

    # ----- GitHub -----
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")
    github_api_base: str = Field(default="https://api.github.com", alias="GITHUB_API_BASE")

    # ----- Persistence -----
    database_url: str = Field(
        default=f"sqlite:///{PROJECT_ROOT / 'data' / 'triage.db'}",
        alias="DATABASE_URL",
    )

    # ----- Approval / change policy -----
    changed_files_threshold: int = Field(default=10, alias="CHANGED_FILES_THRESHOLD")
    approve_mode: str = Field(
        default="manual", alias="APPROVE_MODE"
    )  # "manual" | "auto"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
