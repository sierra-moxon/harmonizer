"""Tests for the orchestrator prompts and loop (Phase 6).

The prompt tests use a real, seeded :class:`MappingState` (no mocks). The loop
tests stub the agent with a fake :class:`AbstractAgent` so nothing spawns the
real Claude CLI; the fake records outcomes into the DB the way the MCP ledger
tools would, so the loop sees progress and stops early.
"""

from __future__ import annotations

import asyncio

import pytest

from harmonizer.agent.base import AbstractAgent, IterationResult
from harmonizer.agent.skills import ENABLED_SKILLS
from harmonizer.database.models import JobStatus
from harmonizer.orchestrator import loop as loop_mod
from harmonizer.orchestrator.loop import (
    _is_fresh,
    _mtime_or_none,
    run_mapping_async,
)
from harmonizer.orchestrator.prompts import (
    build_initial_prompt,
    build_iteration_prompt,
    build_system_prompt,
)
from harmonizer.state.mapping_state import MappingState, PlaceholderEntry


# -- fixtures ------------------------------------------------------------------


def _seed(job_id: str = "job-1") -> MappingState:
    state = MappingState(
        job_id=job_id,
        source_filename="sample.tsv",
        interface_guess="SoilInterface",
        max_iterations=5,
    )
    state.placeholders = [
        PlaceholderEntry(
            row_id="*", column="env_broad_scale", proposed_slot="env_broad_scale"
        ),
        PlaceholderEntry(
            row_id="*", column="env_local_scale", proposed_slot="env_local_scale"
        ),
    ]
    return state


# -- build_system_prompt -------------------------------------------------------


def test_system_prompt_lists_enabled_skills():
    prompt = build_system_prompt()
    for name in ENABLED_SKILLS:
        assert name in prompt
    # Points at where the skills are materialized.
    assert ".claude/skills" in prompt


def test_system_prompt_references_real_tool_signatures():
    prompt = build_system_prompt()
    # Ledger tools take `column` first (authoritative signatures, not drifted).
    assert "record_mapping(column, slot, value" in prompt
    assert "leave_placeholder(column, reason" in prompt
    assert "validate_value(slot, value, interface=None)" in prompt


# -- build_iteration_prompt ----------------------------------------------------


def test_iteration_prompt_lists_outstanding_placeholders():
    state = _seed()
    prompt = build_iteration_prompt(state, iteration=2)

    assert "Iteration 2" in prompt
    assert "Outstanding placeholders (2)" in prompt
    assert "env_broad_scale" in prompt
    assert "env_local_scale" in prompt


def test_iteration_prompt_reflects_resolution_progress():
    state = _seed()
    state.record_mapping(
        row_id="*",
        column="env_broad_scale",
        slot="env_broad_scale",
        value="terrestrial biome [ENVO:00000446]",
    )
    prompt = build_iteration_prompt(state, iteration=3)

    assert "Outstanding placeholders (1)" in prompt
    assert "env_local_scale" in prompt
    # The resolved column is no longer listed as outstanding.
    assert "column 'env_broad_scale'" not in prompt


def test_iteration_prompt_when_nothing_remains():
    state = _seed()
    for entry in list(state.placeholders):
        state.record_mapping(
            row_id=entry.row_id,
            column=entry.column,
            slot=entry.column,
            value="x",
        )
    prompt = build_iteration_prompt(state, iteration=4)
    assert "No placeholders remain" in prompt


def test_iteration_prompt_references_tools():
    prompt = build_iteration_prompt(_seed(), iteration=2)
    assert "record_mapping(column, slot, value" in prompt
    assert "leave_placeholder(column, reason" in prompt
    assert "execute_code(code)" in prompt


# -- build_initial_prompt ------------------------------------------------------


def test_initial_prompt_references_sidecars_and_context():
    state = _seed()
    prompt = build_initial_prompt(state, study_context="Arctic soil cores, 2021.")

    assert "draft_mapping.json" in prompt
    assert "curation_inputs.json" in prompt
    assert "curation_report.json" in prompt
    assert "SoilInterface" in prompt
    assert "Arctic soil cores" in prompt
    assert "Outstanding placeholders (2)" in prompt


# -- freshness guard -----------------------------------------------------------


def test_is_fresh_true_when_newly_created(tmp_path):
    path = tmp_path / "curation_report.json"
    assert not path.exists()
    baseline = _mtime_or_none(path)  # None
    path.write_text("{}")
    assert _is_fresh(path, baseline) is True


def test_is_fresh_false_when_unchanged(tmp_path):
    path = tmp_path / "curation_report.json"
    path.write_text("{}")
    baseline = _mtime_or_none(path)
    # No new write => not fresh relative to the captured baseline.
    assert _is_fresh(path, baseline) is False


def test_is_fresh_true_when_rewritten_after_baseline(tmp_path):
    path = tmp_path / "curation_report.json"
    path.write_text("{}")
    baseline = _mtime_or_none(path)
    import os

    os.utime(path, (baseline + 10, baseline + 10))
    assert _is_fresh(path, baseline) is True


def test_is_fresh_false_when_missing(tmp_path):
    path = tmp_path / "missing.json"
    assert _is_fresh(path, None) is False


# -- loop (with a fake agent) --------------------------------------------------


class FakeAgent(AbstractAgent):
    """A canned agent that resolves one placeholder per turn via the DB.

    It mimics the MCP ledger tools: each turn it loads the job's state, resolves
    the first remaining placeholder, rewrites ``curation_report.json`` (so the
    freshness guard is satisfied), and persists. Records the prompts it saw.
    """

    def __init__(self, config, session_factory):
        super().__init__(config)
        self.session_factory = session_factory
        self.prompts: list[str] = []
        self.reset_flags: list[bool] = []
        self.prepared = False

    def prepare_job_workspace(self) -> None:
        self.prepared = True
        self.config.job_dir.mkdir(parents=True, exist_ok=True)

    async def run_iteration(self, prompt, reset_session=False):
        self.prompts.append(prompt)
        self.reset_flags.append(reset_session)

        state = MappingState.load_from_database_sync(
            self.config.job_id, self.session_factory
        )
        remaining = state.remaining_placeholders()
        if remaining:
            entry = remaining[0]
            state.record_mapping(
                row_id=entry.row_id,
                column=entry.column,
                slot=entry.column,
                value="resolved-value",
            )
            state.save_to_database_sync(self.session_factory)

        # Mimic the ledger tools rewriting the report so freshness passes.
        report = self.config.job_dir / "curation_report.json"
        report.write_text('{"rewritten": true}')

        return IterationResult(text="ok", is_error=False, num_turns=1)


def _seed_job_in_db(session_factory, job_dir, max_iterations=5):
    state = _seed()
    state.max_iterations = max_iterations
    state.save_to_database_sync(session_factory)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "draft_mapping.json").write_text('{"job_id": "job-1"}')
    (job_dir / "curation_inputs.json").write_text('{"study_context": "ctx"}')
    return state


def test_loop_resolves_all_and_completes(tmp_path, session_factory):
    job_dir = tmp_path / "jobs" / "job-1"
    _seed_job_in_db(session_factory, job_dir)

    agent = FakeAgent.__new__(FakeAgent)
    # Build the fake with a config it can use; construct via a light AgentConfig.
    from harmonizer.agent.base import AgentConfig
    from harmonizer.providers import AnthropicProvider

    config = AgentConfig(
        job_id="job-1",
        job_dir=job_dir,
        provider=AnthropicProvider(api_key="sk-test"),
    )
    agent = FakeAgent(config, session_factory)

    final = asyncio.run(
        run_mapping_async(
            job_dir,
            session_factory=session_factory,
            agent=agent,
        )
    )

    assert agent.prepared is True
    assert final.status == JobStatus.COMPLETED
    assert final.remaining_placeholders() == []
    # Two placeholders => stops after two turns (early stop, well under max=5).
    assert len(agent.prompts) == 2
    # First turn uses the initial prompt and resets the session.
    assert agent.reset_flags[0] is True
    assert "draft_mapping.json" in agent.prompts[0]
    # Later turns use the iteration prompt.
    assert "Iteration 3" in agent.prompts[1]


def test_loop_first_turn_uses_initial_prompt_indexing(tmp_path, session_factory):
    """Iteration indexing: agent loop starts at 2 (iteration 1 is the pre-pass)."""
    job_dir = tmp_path / "jobs" / "job-1"
    _seed_job_in_db(session_factory, job_dir, max_iterations=5)

    from harmonizer.agent.base import AgentConfig
    from harmonizer.providers import AnthropicProvider

    agent = FakeAgent(
        AgentConfig(
            job_id="job-1",
            job_dir=job_dir,
            provider=AnthropicProvider(api_key="sk-test"),
        ),
        session_factory,
    )
    final = asyncio.run(
        run_mapping_async(job_dir, session_factory=session_factory, agent=agent)
    )
    # current_iteration advanced to the last agent iteration that ran (2 then 3).
    assert final.current_iteration == 3


def test_loop_freshness_guard_retries_when_artifact_stale(
    tmp_path, session_factory, monkeypatch
):
    """A turn that never writes the report triggers a freshness retry."""
    job_dir = tmp_path / "jobs" / "job-1"
    _seed_job_in_db(session_factory, job_dir, max_iterations=3)

    from harmonizer.agent.base import AgentConfig
    from harmonizer.providers import AnthropicProvider

    class StaleThenFreshAgent(FakeAgent):
        async def run_iteration(self, prompt, reset_session=False):
            self.prompts.append(prompt)
            self.reset_flags.append(reset_session)
            # On the very first attempt, do NOT write the report (stale turn).
            if len(self.prompts) == 1:
                return IterationResult(text="did nothing", is_error=False)
            # Subsequent attempts behave like the normal fake.
            state = MappingState.load_from_database_sync(
                self.config.job_id, self.session_factory
            )
            remaining = state.remaining_placeholders()
            if remaining:
                entry = remaining[0]
                state.record_mapping(
                    row_id=entry.row_id,
                    column=entry.column,
                    slot=entry.column,
                    value="v",
                )
                state.save_to_database_sync(self.session_factory)
            (self.config.job_dir / "curation_report.json").write_text('{"ok": 1}')
            return IterationResult(text="ok", is_error=False)

    agent = StaleThenFreshAgent(
        AgentConfig(
            job_id="job-1",
            job_dir=job_dir,
            provider=AnthropicProvider(api_key="sk-test"),
        ),
        session_factory,
    )
    final = asyncio.run(
        run_mapping_async(job_dir, session_factory=session_factory, agent=agent)
    )
    assert final.status == JobStatus.COMPLETED
    # The first turn's stale attempt is followed by a freshness retry prompt.
    assert any("was not" in p for p in agent.prompts)


def test_loop_marks_failed_on_agent_error(tmp_path, session_factory):
    job_dir = tmp_path / "jobs" / "job-1"
    _seed_job_in_db(session_factory, job_dir)

    from harmonizer.agent.base import AgentConfig
    from harmonizer.providers import AnthropicProvider

    class BoomAgent(FakeAgent):
        async def run_iteration(self, prompt, reset_session=False):
            raise RuntimeError("boom")

    agent = BoomAgent(
        AgentConfig(
            job_id="job-1",
            job_dir=job_dir,
            provider=AnthropicProvider(api_key="sk-test"),
        ),
        session_factory,
    )
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(
            run_mapping_async(job_dir, session_factory=session_factory, agent=agent)
        )

    reloaded = MappingState.load_from_database_sync("job-1", session_factory)
    assert reloaded.status == JobStatus.FAILED
    assert "boom" in (reloaded.error or "")
