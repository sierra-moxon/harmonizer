"""Agent abstraction: config, per-iteration result, and the ``AbstractAgent`` ABC.

The orchestrator (Phase 6) owns the loop; the agent owns a single turn. An agent
is constructed from an :class:`AgentConfig` (which job, which directory, which
provider) and exposes two operations: :meth:`AbstractAgent.prepare_job_workspace`
(idempotent workspace setup) and :meth:`AbstractAgent.run_iteration` (run one
turn against a prompt, optionally resetting the conversation session).

Mirrors OpenScientist's ``AbstractAgent`` (pattern only; authored here).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harmonizer.providers.base import Provider

#: SDK permission mode literal; ``bypassPermissions`` lets the agent use its MCP
#: tools (including ``execute_code``) autonomously within the controlled job.
DEFAULT_PERMISSION_MODE = "bypassPermissions"


@dataclass
class AgentConfig:
    """Everything an agent needs for one job."""

    job_id: str
    job_dir: Path
    provider: Provider
    model: str | None = None
    system_prompt: str | None = None
    max_turns: int | None = None
    permission_mode: str = DEFAULT_PERMISSION_MODE
    agent_type: str = "claude_code"
    database_url: str | None = None


@dataclass
class IterationResult:
    """The outcome of a single agent turn."""

    text: str
    is_error: bool = False
    num_turns: int = 0
    session_id: str | None = None
    total_cost_usd: float | None = None
    raw: list[Any] = field(default_factory=list)


class AbstractAgent(ABC):
    """Runs one mapping turn with the MCP toolset attached."""

    def __init__(self, config: AgentConfig):
        self.config = config

    @abstractmethod
    def prepare_job_workspace(self) -> None:
        """Ensure the job workspace exists and is ready for a run (idempotent)."""

    @abstractmethod
    async def run_iteration(
        self, prompt: str, reset_session: bool = False
    ) -> IterationResult:
        """Run one turn against ``prompt``; ``reset_session`` starts fresh."""
