"""CBORG (LBNL) provider: Claude models via the CBORG gateway.

CBORG (https://cborg.lbl.gov) is LBNL's LiteLLM-backed gateway. Claude Code talks
to it through the Anthropic *bearer* auth path — ``ANTHROPIC_BASE_URL`` plus
``ANTHROPIC_AUTH_TOKEN`` — rather than the ``ANTHROPIC_API_KEY`` (x-api-key)
scheme the direct-Anthropic provider uses. Selected with ``HARMONIZER_PROVIDER=cborg``.
"""

from __future__ import annotations

from typing import ClassVar

from harmonizer.providers.base import Provider, ProviderConfigError
from harmonizer.settings import Settings, get_settings

#: Claude Code reads these for the gateway base URL and bearer token.
ANTHROPIC_BASE_URL_ENV = "ANTHROPIC_BASE_URL"
ANTHROPIC_AUTH_TOKEN_ENV = "ANTHROPIC_AUTH_TOKEN"

#: Public CBORG endpoint (LBL-network users may override with ``api-local``).
DEFAULT_BASE_URL = "https://api.cborg.lbl.gov"
#: CBORG model id used when no ``HARMONIZER_MODEL`` is set.
DEFAULT_MODEL = "claude-opus-4-8"


class CborgProvider(Provider):
    """Reads ``CBORG_API_KEY`` and routes Claude Code at the CBORG gateway."""

    id: ClassVar[str] = "cborg"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self._api_key = api_key
        self._model = model or DEFAULT_MODEL
        self._base_url = base_url or DEFAULT_BASE_URL

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "CborgProvider":
        """Build a provider from :class:`Settings` (defaults to the cached ones)."""
        settings = settings or get_settings()
        return cls(
            api_key=settings.cborg_api_key,
            model=settings.model,
            base_url=settings.cborg_base_url,
        )

    @property
    def model(self) -> str | None:
        return self._model

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def api_key(self) -> str:
        if not self._api_key:
            raise ProviderConfigError(
                "CBORG_API_KEY is not set; cannot run the CBORG provider"
            )
        return self._api_key

    def environment(self) -> dict[str, str]:
        # Only the bearer header is set (no ANTHROPIC_API_KEY), matching CBORG's
        # documented Claude Code configuration.
        return {
            ANTHROPIC_BASE_URL_ENV: self._base_url,
            ANTHROPIC_AUTH_TOKEN_ENV: self.api_key(),
            "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1",
            "DISABLE_TELEMETRY": "1",
        }
