"""Provider ABC: the thin contract the agent depends on."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar


class ProviderConfigError(RuntimeError):
    """Raised when a provider is used without the credentials it requires."""


class Provider(ABC):
    """Supplies model id, credentials, and agent-subprocess environment vars."""

    #: Stable identifier the factory and settings dispatch on.
    id: ClassVar[str]

    @property
    @abstractmethod
    def model(self) -> str | None:
        """Model id to run (``None`` defers to the CLI/provider default)."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return ``True`` if the credentials needed to run are present."""

    @abstractmethod
    def api_key(self) -> str:
        """Return the API key, raising :class:`ProviderConfigError` if unset."""

    @abstractmethod
    def environment(self) -> dict[str, str]:
        """Environment variables to inject into the agent subprocess."""
