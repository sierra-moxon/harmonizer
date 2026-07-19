"""Tests for the Anthropic provider and settings wiring (Phase 4)."""

from __future__ import annotations

import pytest

from harmonizer.providers import AnthropicProvider, ProviderConfigError, get_provider
from harmonizer.settings import Settings


def _settings(**env: str) -> Settings:
    """Build fresh (uncached) settings from an explicit env mapping."""
    return Settings(_env_file=None, **env)


def test_from_settings_reads_key_and_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("HARMONIZER_MODEL", "claude-test-model")
    settings = Settings(_env_file=None)

    provider = AnthropicProvider.from_settings(settings)

    assert provider.is_configured()
    assert provider.model == "claude-test-model"
    assert provider.api_key() == "sk-test"
    assert provider.environment() == {"ANTHROPIC_API_KEY": "sk-test"}


def test_api_key_raises_when_unset():
    provider = AnthropicProvider(api_key=None)

    assert not provider.is_configured()
    with pytest.raises(ProviderConfigError):
        provider.api_key()
    with pytest.raises(ProviderConfigError):
        provider.environment()


def test_model_defaults_to_none():
    provider = AnthropicProvider(api_key="sk-test")
    assert provider.model is None


def test_get_provider_dispatches_on_settings():
    settings = _settings(provider="anthropic", anthropic_api_key="sk-test")
    provider = get_provider(settings)
    assert isinstance(provider, AnthropicProvider)
    assert provider.id == "anthropic"


def test_get_provider_rejects_unknown():
    settings = _settings(provider="nope")
    with pytest.raises(ValueError, match="unknown provider"):
        get_provider(settings)
