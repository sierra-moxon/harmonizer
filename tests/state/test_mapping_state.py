"""Tests for MappingState load/save round-trip and mutators (Phase 1)."""

import pytest

from harmonizer.database.models import JobStatus, PlaceholderOutcome
from harmonizer.state.mapping_state import MappingState, PlaceholderEntry


def _seed(job_id: str = "job-1") -> MappingState:
    state = MappingState(
        job_id=job_id,
        source_filename="sample.tsv",
        interface_guess="SoilInterface",
        max_iterations=5,
    )
    state.placeholders = [
        PlaceholderEntry(row_id="0", column="env_broad_scale"),
        PlaceholderEntry(row_id="0", column="env_local_scale"),
    ]
    return state


def test_round_trip(session_factory):
    state = _seed()
    state.save_to_database_sync(session_factory)

    reloaded = MappingState.load_from_database_sync("job-1", session_factory)
    assert reloaded.job_id == "job-1"
    assert reloaded.source_filename == "sample.tsv"
    assert reloaded.interface_guess == "SoilInterface"
    assert reloaded.status == JobStatus.PENDING
    assert reloaded.max_iterations == 5
    assert len(reloaded.placeholders) == 2
    assert {p.column for p in reloaded.placeholders} == {
        "env_broad_scale",
        "env_local_scale",
    }


def test_load_unknown_job_raises(session_factory):
    with pytest.raises(ValueError):
        MappingState.load_from_database_sync("nope", session_factory)


def test_record_mapping_marks_resolved(session_factory):
    state = _seed()
    state.record_mapping(
        row_id="0",
        column="env_broad_scale",
        slot="env_broad_scale",
        value="terrestrial biome [ENVO:00000446]",
        evidence=[{"source": "runoak", "quote_or_paraphrase": "biome match"}],
        confidence=0.95,
    )
    state.save_to_database_sync(session_factory)

    reloaded = MappingState.load_from_database_sync("job-1", session_factory)
    entry = next(
        p for p in reloaded.placeholders if p.column == "env_broad_scale"
    )
    assert entry.outcome == PlaceholderOutcome.RESOLVED
    assert entry.value == "terrestrial biome [ENVO:00000446]"
    assert entry.confidence == 0.95
    assert entry.evidence[0]["source"] == "runoak"


def test_leave_placeholder_marks_left(session_factory):
    state = _seed()
    state.leave_placeholder(
        row_id="0",
        column="env_local_scale",
        reason="ambiguous header; no confident ontology match",
    )
    state.save_to_database_sync(session_factory)

    reloaded = MappingState.load_from_database_sync("job-1", session_factory)
    entry = next(
        p for p in reloaded.placeholders if p.column == "env_local_scale"
    )
    assert entry.outcome == PlaceholderOutcome.LEFT_PLACEHOLDER
    assert "ambiguous" in entry.reason


def test_remaining_placeholders(session_factory):
    state = _seed()
    assert len(state.remaining_placeholders()) == 2
    state.record_mapping(
        row_id="0",
        column="env_broad_scale",
        slot="env_broad_scale",
        value="x",
    )
    remaining = state.remaining_placeholders()
    assert len(remaining) == 1
    assert remaining[0].column == "env_local_scale"


def test_record_mapping_creates_entry_when_absent(session_factory):
    state = MappingState(job_id="job-3", source_filename="s.tsv")
    state.record_mapping(
        row_id="2", column="depth", slot="depth", value="10 cm"
    )
    assert len(state.placeholders) == 1
    state.save_to_database_sync(session_factory)

    reloaded = MappingState.load_from_database_sync("job-3", session_factory)
    assert reloaded.placeholders[0].column == "depth"
    assert reloaded.placeholders[0].outcome == PlaceholderOutcome.RESOLVED


def test_save_updates_existing_rows_without_duplicating(session_factory):
    state = _seed()
    state.save_to_database_sync(session_factory)

    # Mutate and re-save; should update in place, not create new rows.
    state.record_mapping(
        row_id="0", column="env_broad_scale", slot="env_broad_scale", value="y"
    )
    state.current_iteration = 3
    state.status = JobStatus.RUNNING
    state.save_to_database_sync(session_factory)

    reloaded = MappingState.load_from_database_sync("job-1", session_factory)
    assert len(reloaded.placeholders) == 2
    assert reloaded.current_iteration == 3
    assert reloaded.status == JobStatus.RUNNING
