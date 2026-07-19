"""Tests for the agent factory and Claude Code option wiring (Phase 4).

Option assembly and workspace prep are unit-tested without spawning the CLI. A
single live iteration is exercised only when ``ANTHROPIC_API_KEY`` is set (and
skipped if the Claude CLI is unavailable).
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from harmonizer.agent import AgentConfig, ClaudeCodeAgent, get_agent
from harmonizer.agent.claude_code_agent import DATABASE_URL_ENV, MCP_SERVER_NAME
from harmonizer.providers import AnthropicProvider, get_provider
from harmonizer.settings import Settings
from harmonizer_tools.state import JOB_DIR_ENV, JOB_ID_ENV


def _config(tmp_path, **overrides) -> AgentConfig:
    provider = overrides.pop(
        "provider", AnthropicProvider(api_key="sk-test", model="provider-model")
    )
    kwargs = dict(
        job_id="job-agent",
        job_dir=tmp_path / "jobs" / "job-agent",
        provider=provider,
        database_url="sqlite:///test.db",
    )
    kwargs.update(overrides)
    return AgentConfig(**kwargs)


def test_get_agent_returns_claude_code_agent(tmp_path):
    agent = get_agent(_config(tmp_path))
    assert isinstance(agent, ClaudeCodeAgent)


def test_get_agent_rejects_unknown_type(tmp_path):
    with pytest.raises(ValueError, match="unknown agent type"):
        get_agent(_config(tmp_path, agent_type="mystery"))


def test_prepare_job_workspace_creates_dir(tmp_path):
    agent = get_agent(_config(tmp_path))
    assert not agent.config.job_dir.exists()
    agent.prepare_job_workspace()
    assert agent.config.job_dir.is_dir()


def test_mcp_servers_bind_job_context(tmp_path):
    agent = get_agent(_config(tmp_path))
    servers = agent._mcp_servers()

    assert set(servers) == {MCP_SERVER_NAME}
    server = servers[MCP_SERVER_NAME]
    assert server["type"] == "stdio"
    assert server["command"] == sys.executable
    assert server["args"] == ["-m", "harmonizer_tools"]
    assert server["env"][JOB_ID_ENV] == "job-agent"
    assert server["env"][JOB_DIR_ENV] == str(agent.config.job_dir)
    assert server["env"][DATABASE_URL_ENV] == "sqlite:///test.db"


def test_build_options_wires_provider_and_model(tmp_path):
    agent = get_agent(_config(tmp_path, model="explicit-model"))
    options = agent._build_options()

    assert options.cwd == str(agent.config.job_dir)
    assert options.model == "explicit-model"
    assert options.env == {"ANTHROPIC_API_KEY": "sk-test"}
    assert MCP_SERVER_NAME in options.mcp_servers
    assert options.resume is None


def test_build_options_falls_back_to_provider_model(tmp_path):
    agent = get_agent(_config(tmp_path, model=None))
    options = agent._build_options()
    assert options.model == "provider-model"


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; skipping live agent smoke test",
)
def test_live_iteration_reaches_schema_tools(tmp_path):
    from claude_agent_sdk import CLINotFoundError

    provider = get_provider(Settings(_env_file=None))
    config = AgentConfig(
        job_id="job-smoke",
        job_dir=tmp_path / "jobs" / "job-smoke",
        provider=provider,
        max_turns=3,
    )
    agent = get_agent(config)
    agent.prepare_job_workspace()

    prompt = (
        "Use your list_interfaces tool to list the available interfaces, then "
        "reply with how many there are."
    )
    try:
        result = asyncio.run(agent.run_iteration(prompt))
    except CLINotFoundError:
        pytest.skip("Claude CLI not available")

    assert not result.is_error
    assert result.text.strip()
