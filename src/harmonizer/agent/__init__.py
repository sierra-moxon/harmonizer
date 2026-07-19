"""Agent executor: run one mapping turn with the MCP toolset attached.

The orchestrator (Phase 6) owns iteration; the agent owns a single turn. The
:mod:`harmonizer.agent.base` module defines the :class:`AgentConfig`,
:class:`IterationResult`, and :class:`AbstractAgent` contract;
:mod:`harmonizer.agent.claude_code_agent` implements it against
``claude-agent-sdk``; :func:`get_agent` dispatches on ``AgentConfig.agent_type``.

Mirrors OpenScientist's ``agent`` package (pattern only; authored here).
"""

from __future__ import annotations

from harmonizer.agent.base import (
    AbstractAgent,
    AgentConfig,
    IterationResult,
)
from harmonizer.agent.claude_code_agent import ClaudeCodeAgent
from harmonizer.agent.factory import get_agent
from harmonizer.agent.skills import write_skills_to_claude_dir

__all__ = [
    "AbstractAgent",
    "AgentConfig",
    "ClaudeCodeAgent",
    "IterationResult",
    "get_agent",
    "write_skills_to_claude_dir",
]
