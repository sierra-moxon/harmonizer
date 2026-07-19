"""Claude Code agent: wraps ``claude-agent-sdk`` with the harmonizer MCP toolset.

The agent launches the Claude Code CLI (via ``claude-agent-sdk``) with the
``harmonizer_tools`` MCP server attached as a stdio subprocess. The subprocess is
bound to this job through the ``HARMONIZER_JOB_*`` environment variables so its
tools round-trip against the same job directory and database. Session continuity
across turns is kept via the SDK ``resume`` id; ``reset_session`` drops it.

Mirrors OpenScientist's ``claude_code_agent`` (pattern only; authored here).
"""

from __future__ import annotations

import os
import sys

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from harmonizer.agent.base import AbstractAgent, IterationResult
from harmonizer.agent.skills import write_skills_to_claude_dir
from harmonizer.database.session import get_database_url
from harmonizer_tools.state import JOB_DIR_ENV, JOB_ID_ENV

#: Name the MCP server is registered under; tools appear as
#: ``mcp__harmonizer__<tool>`` to the agent.
MCP_SERVER_NAME = "harmonizer"

#: Env var the MCP subprocess reads to reach the same database as the pre-pass.
DATABASE_URL_ENV = "HARMONIZER_DATABASE_URL"


class ClaudeCodeAgent(AbstractAgent):
    """Run mapping turns through the Claude Code SDK with MCP tools wired in."""

    def __init__(self, config):
        super().__init__(config)
        self._session_id: str | None = None

    # -- workspace -------------------------------------------------------------

    def prepare_job_workspace(self) -> None:
        """Ensure the job directory exists and materialize the workflow skills.

        The mapping methodology is delivered as Claude Code skills written into
        ``<job_dir>/.claude/skills/`` so the CLI discovers them for this run.
        """
        self.config.job_dir.mkdir(parents=True, exist_ok=True)
        write_skills_to_claude_dir(self.config.job_dir)

    # -- option assembly -------------------------------------------------------

    def _mcp_servers(self) -> dict[str, dict]:
        """Return the stdio config for the ``harmonizer_tools`` MCP subprocess."""
        env = {
            JOB_ID_ENV: self.config.job_id,
            JOB_DIR_ENV: str(self.config.job_dir),
        }
        database_url = self.config.database_url or os.environ.get(DATABASE_URL_ENV)
        if database_url is None:
            database_url = get_database_url()
        env[DATABASE_URL_ENV] = database_url
        return {
            MCP_SERVER_NAME: {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", "harmonizer_tools"],
                "env": env,
            }
        }

    def _build_options(self) -> ClaudeAgentOptions:
        """Assemble :class:`ClaudeAgentOptions` for one turn."""
        return ClaudeAgentOptions(
            cwd=str(self.config.job_dir),
            model=self.config.model or self.config.provider.model,
            system_prompt=self.config.system_prompt,
            max_turns=self.config.max_turns,
            permission_mode=self.config.permission_mode,
            mcp_servers=self._mcp_servers(),
            env=self.config.provider.environment(),
            resume=self._session_id,
            # Do not inherit the developer's own user/project CLI settings.
            setting_sources=None,
        )

    # -- execution -------------------------------------------------------------

    async def run_iteration(
        self, prompt: str, reset_session: bool = False
    ) -> IterationResult:
        """Run one turn; carries the session forward unless ``reset_session``."""
        if reset_session:
            self._session_id = None

        options = self._build_options()
        text_parts: list[str] = []
        messages: list = []
        result: ResultMessage | None = None

        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                messages.append(message)
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                elif isinstance(message, ResultMessage):
                    result = message

        if result is not None and result.session_id:
            self._session_id = result.session_id

        return IterationResult(
            text="".join(text_parts),
            is_error=bool(result.is_error) if result is not None else False,
            num_turns=result.num_turns if result is not None else 0,
            session_id=self._session_id,
            total_cost_usd=result.total_cost_usd if result is not None else None,
            raw=messages,
        )
