"""Application settings via ``pydantic-settings``, cached with ``lru_cache``.

Configuration is read from the environment (and an optional ``.env`` file) with
the ``HARMONIZER_`` prefix. The provider API key is the one exception: it is read
from the vendor-standard ``ANTHROPIC_API_KEY`` (falling back to the prefixed
form) so existing tooling keeps working.

Mirrors OpenScientist's cached-settings pattern (pattern only; authored here).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration: provider id, credentials, model, container flags."""

    model_config = SettingsConfigDict(
        env_prefix="HARMONIZER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #: Provider identifier the agent factory dispatches on.
    provider: str = "anthropic"
    #: Model id passed to the agent (``None`` defers to the CLI/provider default).
    model: str | None = None
    #: Vendor API key; read from ``ANTHROPIC_API_KEY`` first.
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ANTHROPIC_API_KEY", "HARMONIZER_ANTHROPIC_API_KEY"
        ),
    )
    #: Default iteration budget for the orchestrator loop (Phase 6).
    max_iterations: int = 10
    #: Feature flag for Docker per-job isolation (Phase 9); off for local dev.
    use_container_isolation: bool = False
    #: Root directory under which per-job directories are created. Mirrors the
    #: ``HARMONIZER_JOBS_ROOT`` env var the pre-pass already honors so the web
    #: layer and the CLI agree on where jobs live.
    jobs_root: str = "jobs"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings`."""
    return Settings()
