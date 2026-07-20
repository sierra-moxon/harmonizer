"""Tests for the CBORG provider and settings wiring."""

from __future__ import annotations

import pytest

from harmonizer.providers import CborgProvider, ProviderConfigError, get_provider
from harmonizer.providers.cborg import DEFAULT_BASE_URL, DEFAULT_MODEL
from harmonizer.settings import Settings


def _settings(**env: str) -> Settings:
    """Build fresh (uncached) settings from an explicit env mapping."""
    return Settings(_env_file=None, **env)


def test_from_settings_reads_key_and_defaults(monkeypatch):
    monkeypatch.setenv("CBORG_API_KEY", "cborg-test")
    settings = Settings(_env_file=None)

    provider = CborgProvider.from_settings(settings)

    assert provider.is_configured()
    assert provider.model == DEFAULT_MODEL  # claude-opus-4-8
    assert provider.api_key() == "cborg-test"
    assert provider.environment() == {
        "ANTHROPIC_BASE_URL": DEFAULT_BASE_URL,
        "ANTHROPIC_AUTH_TOKEN": "cborg-test",
        "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1",
        "DISABLE_TELEMETRY": "1",
    }


def test_environment_uses_bearer_not_api_key():
    provider = CborgProvider(api_key="cborg-test")
    env = provider.environment()
    assert env["ANTHROPIC_AUTH_TOKEN"] == "cborg-test"
    # CBORG must not receive an x-api-key header.
    assert "ANTHROPIC_API_KEY" not in env


def test_model_override_and_base_url_override(monkeypatch):
    monkeypatch.setenv("CBORG_API_KEY", "cborg-test")
    monkeypatch.setenv("HARMONIZER_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("CBORG_BASE_URL", "https://api-local.cborg.lbl.gov")
    settings = Settings(_env_file=None)

    provider = CborgProvider.from_settings(settings)

    assert provider.model == "claude-sonnet-4-6"
    assert provider.environment()["ANTHROPIC_BASE_URL"] == (
        "https://api-local.cborg.lbl.gov"
    )


def test_api_key_raises_when_unset():
    provider = CborgProvider(api_key=None)

    assert not provider.is_configured()
    with pytest.raises(ProviderConfigError):
        provider.api_key()
    with pytest.raises(ProviderConfigError):
        provider.environment()


def test_get_provider_dispatches_on_settings():
    settings = _settings(provider="cborg", cborg_api_key="cborg-test")
    provider = get_provider(settings)
    assert isinstance(provider, CborgProvider)
    assert provider.id == "cborg"
