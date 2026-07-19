"""Provider abstraction: credentials, model selection, and agent env vars.

A :class:`~harmonizer.providers.base.Provider` supplies the pieces the agent
needs to talk to a model backend without hard-coding a vendor: the model id, the
API key (raising when unset), and the environment variables to inject into the
agent subprocess. Only Anthropic is implemented; add more providers when needed.

Mirrors OpenScientist's provider abstraction (pattern only; authored here).
"""

from __future__ import annotations

from harmonizer.providers.anthropic import AnthropicProvider
from harmonizer.providers.base import Provider, ProviderConfigError
from harmonizer.settings import Settings, get_settings

__all__ = [
    "AnthropicProvider",
    "Provider",
    "ProviderConfigError",
    "get_provider",
]

#: Registry of known providers keyed by their stable ``id``.
_PROVIDERS: dict[str, type[Provider]] = {
    AnthropicProvider.id: AnthropicProvider,
}


def get_provider(settings: Settings | None = None) -> Provider:
    """Build the provider named by ``settings.provider``.

    Raises :class:`ValueError` for an unknown provider id.
    """
    settings = settings or get_settings()
    provider_cls = _PROVIDERS.get(settings.provider)
    if provider_cls is None:
        known = ", ".join(sorted(_PROVIDERS))
        raise ValueError(
            f"unknown provider {settings.provider!r} (known: {known})"
        )
    return provider_cls.from_settings(settings)
