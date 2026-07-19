"""Anthropic (Claude-compatible) provider."""

from __future__ import annotations

from typing import ClassVar

from harmonizer.providers.base import Provider, ProviderConfigError
from harmonizer.settings import Settings, get_settings

#: Environment variable the Claude Code CLI reads for its API key.
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"


class AnthropicProvider(Provider):
    """Reads ``ANTHROPIC_API_KEY`` and selects the model for the Claude backend."""

    id: ClassVar[str] = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key
        self._model = model

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "AnthropicProvider":
        """Build a provider from :class:`Settings` (defaults to the cached ones)."""
        settings = settings or get_settings()
        return cls(api_key=settings.anthropic_api_key, model=settings.model)

    @property
    def model(self) -> str | None:
        return self._model

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def api_key(self) -> str:
        if not self._api_key:
            raise ProviderConfigError(
                f"{ANTHROPIC_API_KEY_ENV} is not set; cannot run the Anthropic "
                "provider"
            )
        return self._api_key

    def environment(self) -> dict[str, str]:
        return {ANTHROPIC_API_KEY_ENV: self.api_key()}
