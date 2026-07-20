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
    #: Image the per-job container runs when isolation is on. Read from
    #: ``HARMONIZER_JOB_IMAGE`` (set in docker-compose.yml) so operators can point
    #: at a custom agent tag without code changes; ``JobContainerRunner`` uses it.
    job_image: str = "harmonizer-agent:latest"
    #: Root directory under which per-job directories are created. Mirrors the
    #: ``HARMONIZER_JOBS_ROOT`` env var the pre-pass already honors so the web
    #: layer and the CLI agree on where jobs live.
    jobs_root: str = "jobs"
    #: Host path of this project directory on the *Docker host*. Set in
    #: docker-compose.yml (``${PWD}``). When container isolation is on, the web
    #: process launches sibling job containers via the mounted socket, so bind
    #: mounts must use *host* paths, not the web container's internal paths;
    #: :func:`~harmonizer.job_container.utils.to_host_path` uses this to
    #: translate ``container_app_dir``-rooted paths back to the host. ``None``
    #: (bare ``docker run`` / local dev) means no translation.
    host_project_dir: str | None = None
    #: The container-internal app dir that ``host_project_dir`` maps to (the
    #: image ``WORKDIR``). Job dirs live under ``<container_app_dir>/jobs``.
    container_app_dir: str = "/app"
    #: Explicit Docker network for sibling job containers. ``None`` auto-detects
    #: the web container's own (compose) network so siblings can reach the
    #: ``postgres`` service by hostname.
    agent_network: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings`."""
    return Settings()
