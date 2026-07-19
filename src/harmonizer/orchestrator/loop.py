"""The mapping iteration loop (orchestrator).

:func:`run_mapping_async` is the entry point the job manager (Phase 8) calls. It
loads the runtime context for a job, prepares the agent workspace, then drives
the placeholder-resolution loop turn by turn:

* **Iteration indexing.** Iteration 1 is the deterministic pre-pass / setup
  (``Job.current_iteration`` is 0 after setup). The agent loop therefore runs
  iterations ``2..max_iterations`` inclusive. The first agent turn (iteration 2)
  uses the kickoff prompt; later turns use the per-turn iteration prompt. Loop
  control — iteration count, retries, outcome interpretation — lives *here*, not
  in the agent.
* **Early stop.** The loop stops as soon as
  :meth:`MappingState.remaining_placeholders` is empty.
* **Freshness guard.** Two uses of the same mechanic. *Per turn*, the loop
  requires the ledger (``curation_report.json``, rewritten by the MCP tools on
  every recorded outcome) to be newer than a baseline captured before the turn;
  otherwise it retries with a nudge prompt up to :data:`MAX_FRESHNESS_RETRIES`
  times (:func:`_is_fresh`). *At report time*, the report phase reuses
  :func:`_is_fresh` to guard the write of the real schema-conformant artifact
  (``mapped_output.json``) — the write the Phase 6 checker asked us to guard
  instead of the always-hot ledger.
* **Report phase seam.** After the loop, :func:`_run_report_phase` delegates to
  :func:`harmonizer.orchestrator.report.run_report_phase`, which emits the
  validated artifact and finalizes the curation report.
* **Final status.** The job is persisted ``completed`` or ``failed`` using the
  :class:`~harmonizer.database.models.JobStatus` enum.

Mirrors OpenScientist's ``orchestrator/discovery.py`` (loop + report + freshness
guard; pattern only, authored here).
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from harmonizer.agent import AgentConfig, IterationResult, get_agent
from harmonizer.agent.base import AbstractAgent
from harmonizer.database.models import JobStatus
from harmonizer.job.setup import CURATION_REPORT_FILENAME
from harmonizer.orchestrator.prompts import (
    build_initial_prompt,
    build_iteration_prompt,
    build_system_prompt,
)
from harmonizer.providers import get_provider
from harmonizer.settings import Settings, get_settings
from harmonizer.state.mapping_state import MappingState

#: The pre-pass / setup occupies iteration 1; the agent loop starts at 2.
FIRST_AGENT_ITERATION = 2

#: How many times to re-prompt when the expected artifact was not (re)written.
MAX_FRESHNESS_RETRIES = 2

#: Report artifact whose freshness is guarded each turn (rewritten by the ledger
#: tools on every ``record_mapping`` / ``leave_placeholder``).
_GUARDED_ARTIFACT = CURATION_REPORT_FILENAME


# -- freshness guard -----------------------------------------------------------


def _mtime_or_none(path: Path) -> float | None:
    """Return ``path``'s mtime, or ``None`` if it does not exist."""
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def _is_fresh(path: Path, baseline_mtime: float | None) -> bool:
    """Return ``True`` if ``path`` was written after ``baseline_mtime``.

    A file that did not exist at baseline (``baseline_mtime is None``) is fresh
    as soon as it exists. An existing file is fresh only when its mtime is
    strictly newer than the baseline captured before the turn.
    """
    current = _mtime_or_none(path)
    if current is None:
        return False
    if baseline_mtime is None:
        return True
    return current > baseline_mtime


# -- runtime context -----------------------------------------------------------


def _load_study_context(job_dir: Path) -> str:
    """Read the study context out of ``curation_inputs.json`` if present."""
    inputs_path = job_dir / "curation_inputs.json"
    try:
        payload = json.loads(inputs_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return ""
    return str(payload.get("study_context", "") or "")


def _resolve_job_id(job_dir: Path) -> str:
    """Determine the job id from the draft-mapping sidecar, else the dir name."""
    draft_path = job_dir / "draft_mapping.json"
    try:
        payload = json.loads(draft_path.read_text())
        job_id = payload.get("job_id")
        if job_id:
            return str(job_id)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return job_dir.name


def _build_agent(
    state: MappingState,
    job_dir: Path,
    settings: Settings,
    database_url: str | None,
) -> AbstractAgent:
    """Build and return the agent for this job (default: Claude Code)."""
    provider = get_provider(settings)
    config = AgentConfig(
        job_id=state.job_id,
        job_dir=job_dir,
        provider=provider,
        model=settings.model,
        system_prompt=build_system_prompt(),
        database_url=database_url,
    )
    return get_agent(config)


# -- report phase seam (Phase 7) ----------------------------------------------


def _run_report_phase(
    state: MappingState,
    job_dir: Path,
    session_factory: sessionmaker[Session] | None,
) -> MappingState:
    """Report-phase seam: delegate to :mod:`harmonizer.orchestrator.report`.

    Phase 7 lives in :func:`harmonizer.orchestrator.report.run_report_phase`,
    which produces the schema-conformant artifact (``mapped_output.json``,
    validated per slot and guarded for freshness) and finalizes the curation
    report from :class:`MappingState`. The loop retains all control and only
    calls this once, after the iteration loop; the report module owns content.
    Imported lazily to avoid a circular import (``report`` imports the freshness
    helpers from this module).
    """
    from harmonizer.orchestrator.report import run_report_phase

    return run_report_phase(state, job_dir, session_factory)


# -- the loop ------------------------------------------------------------------


async def run_mapping_async(
    job_dir: str | Path,
    session_factory: sessionmaker[Session] | None = None,
    settings: Settings | None = None,
    database_url: str | None = None,
    agent: AbstractAgent | None = None,
) -> MappingState:
    """Run the mapping loop for the job rooted at ``job_dir``.

    Loads the job's :class:`MappingState`, prepares the agent workspace, runs the
    placeholder-resolution loop (iterations ``2..max_iterations``, stopping early
    when no placeholders remain), then the report-phase seam, and finally
    persists the job's terminal status. ``agent`` may be injected (e.g. a fake in
    tests) to avoid spawning the real CLI.

    Returns the final (persisted) :class:`MappingState`.
    """
    job_dir = Path(job_dir)
    settings = settings or get_settings()

    job_id = _resolve_job_id(job_dir)
    state = MappingState.load_from_database_sync(job_id, session_factory)
    study_context = _load_study_context(job_dir)

    if agent is None:
        agent = _build_agent(state, job_dir, settings, database_url)
    agent.prepare_job_workspace()

    state.status = JobStatus.RUNNING
    state.save_to_database_sync(session_factory)

    try:
        state = await _iterate(agent, state, job_dir, session_factory)
        state = _run_report_phase(state, job_dir, session_factory)
        state.status = JobStatus.COMPLETED
        state.error = None
    except Exception as exc:  # noqa: BLE001 — record the failure and re-raise.
        state.status = JobStatus.FAILED
        state.error = f"{type(exc).__name__}: {exc}"
        state.save_to_database_sync(session_factory)
        raise
    finally:
        state.save_to_database_sync(session_factory)

    return state


async def _iterate(
    agent: AbstractAgent,
    state: MappingState,
    job_dir: Path,
    session_factory: sessionmaker[Session] | None,
) -> MappingState:
    """Drive iterations ``2..max_iterations``, stopping when nothing remains.

    Returns the latest :class:`MappingState` (with this run's outcomes reloaded
    from the DB and the loop-owned counters advanced).
    """
    artifact = job_dir / _GUARDED_ARTIFACT

    for iteration in range(FIRST_AGENT_ITERATION, state.max_iterations + 1):
        if not state.remaining_placeholders():
            break

        reset_session = iteration == FIRST_AGENT_ITERATION
        if reset_session:
            prompt = build_initial_prompt(
                state, study_context=_load_study_context(job_dir)
            )
        else:
            prompt = build_iteration_prompt(state, iteration=iteration)

        await _run_turn_with_freshness(
            agent, prompt, artifact, reset_session=reset_session
        )

        # The MCP ledger tools mutate the DB directly; reload to see this turn's
        # resolutions/refusals, then advance the iteration counter.
        state = _reload_progress(state, session_factory)
        state.current_iteration = iteration
        state.save_to_database_sync(session_factory)

    return state


def _reload_progress(
    state: MappingState,
    session_factory: sessionmaker[Session] | None,
) -> MappingState:
    """Reload placeholder outcomes written by the MCP tools during a turn.

    Preserves loop-owned fields (status, current_iteration, max_iterations) that
    the loop — not the agent — is authoritative for.
    """
    fresh = MappingState.load_from_database_sync(state.job_id, session_factory)
    fresh.status = state.status
    fresh.max_iterations = state.max_iterations
    fresh.current_iteration = state.current_iteration
    return fresh


async def _run_turn_with_freshness(
    agent: AbstractAgent,
    prompt: str,
    artifact: Path,
    reset_session: bool,
) -> IterationResult:
    """Run one turn, retrying if the guarded artifact was not (re)written.

    Captures the artifact's mtime before the turn and, after the turn, requires
    the artifact to be fresh (:func:`_is_fresh`). If it is stale, re-prompts with
    a nudge up to :data:`MAX_FRESHNESS_RETRIES` times. The first attempt keeps
    ``reset_session``; retries never reset (they continue the same session).
    """
    result: IterationResult | None = None
    for attempt in range(MAX_FRESHNESS_RETRIES + 1):
        baseline = _mtime_or_none(artifact)
        turn_prompt = prompt if attempt == 0 else _freshness_retry_prompt(artifact)
        result = await agent.run_iteration(
            turn_prompt, reset_session=reset_session and attempt == 0
        )
        if _is_fresh(artifact, baseline):
            return result
    return result  # type: ignore[return-value]


def _freshness_retry_prompt(artifact: Path) -> str:
    """The nudge sent when the guarded artifact was not written this turn."""
    return (
        f"You did not record any outcome this turn: `{artifact.name}` was not "
        "updated. Use record_mapping (on success) or leave_placeholder (to "
        "refuse) for at least one outstanding placeholder before you stop."
    )
