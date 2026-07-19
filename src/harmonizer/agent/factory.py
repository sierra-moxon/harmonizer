"""Agent factory: build an :class:`AbstractAgent` from an :class:`AgentConfig`."""

from __future__ import annotations

from harmonizer.agent.base import AbstractAgent, AgentConfig
from harmonizer.agent.claude_code_agent import ClaudeCodeAgent

#: Registry of agent implementations keyed by ``AgentConfig.agent_type``.
_AGENTS: dict[str, type[AbstractAgent]] = {
    "claude_code": ClaudeCodeAgent,
}


def get_agent(config: AgentConfig) -> AbstractAgent:
    """Return the agent for ``config.agent_type``.

    Raises :class:`ValueError` for an unknown agent type.
    """
    agent_cls = _AGENTS.get(config.agent_type)
    if agent_cls is None:
        known = ", ".join(sorted(_AGENTS))
        raise ValueError(
            f"unknown agent type {config.agent_type!r} (known: {known})"
        )
    return agent_cls(config)
