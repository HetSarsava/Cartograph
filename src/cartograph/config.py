"""Central configuration: environment loading, provider keys and test limits.

Backed by `.env` (see `.env.example` for the full variable list) and process
environment variables, with real environment variables taking precedence
over `.env`. Access via `get_settings()`, which caches a single instance for
the process.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for Cartograph."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: str | None = Field(
        default=None,
        description="Groq API key. Required to actually call the LLM; deterministic tools work without it.",
    )
    groq_model: str = Field(
        default="openai/gpt-oss-120b",
        description="Default Groq model id. Must support JSON mode — see `groq.models.list()`.",
    )

    test_timeout_seconds: int = Field(
        default=120,
        gt=0,
        validation_alias="CARTOGRAPH_TEST_TIMEOUT_SECONDS",
        description="Timeout (seconds) for a single run_tests() invocation.",
    )
    log_level: str = Field(default="INFO", validation_alias="CARTOGRAPH_LOG_LEVEL")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide `Settings`, loaded once and cached.

    Tests that need different settings should construct `Settings(...)`
    directly rather than mutating environment variables and relying on cache
    invalidation.
    """
    return Settings()
