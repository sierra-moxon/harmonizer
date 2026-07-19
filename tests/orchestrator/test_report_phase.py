"""Tests for the Phase 7 report phase (both outputs).

Uses real instances throughout (a real temp SQLite DB via the ``session_factory``
fixture, the real ``SchemaView`` behind ``validate_value``) rather than mocks,
per the plan's testing conventions. Each test seeds a job on disk + in the DB the
way the pre-pass (Phase 2) and the ledger tools (Phase 3) would, then runs
``run_report_phase`` and asserts on the two emitted files.
"""

from __future__ import annotations

import json
from pathlib import Path

from harmonizer.database.models import PlaceholderOutcome
from harmonizer.job.setup import (
    CURATION_REPORT_FILENAME,
    DRAFT_MAPPING_FILENAME,
    MAPPED_OUTPUT_FILENAME,
)
from harmonizer.orchestrator.report import run_report_phase
from harmonizer.schema.submission_schema import validate_value
from harmonizer.state.mapping_state import MappingState, PlaceholderEntry

# A permissible value and a rejected value for the SoilInterface `soil_horizon`
# enum slot (checked against the real schema in the tests below).
_VALID_HORIZON = "A horizon"
_INVALID_HORIZON = "definitely-not-a-horizon-xyz"


# -- helpers -------------------------------------------------------------------


def _write_draft(job_dir: Path, job_id: str, columns: dict) -> None:
    """Write a ``draft_mapping.json`` like the pre-pass would."""
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / DRAFT_MAPPING_FILENAME).write_text(
        json.dumps(
            {
                "job_id": job_id,
                "source_filename": "sample.tsv",
                "interface": "SoilInterface",
                "columns": columns,
            },
            indent=2,
        )
        + "\n"
    )


def _seed_state(job_id: str, placeholders: list[PlaceholderEntry]) -> MappingState:
    state = MappingState(
        job_id=job_id,
        source_filename="sample.tsv",
        interface_guess="SoilInterface",
        max_iterations=5,
    )
    state.placeholders = placeholders
    return state


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


# -- reconciliation ------------------------------------------------------------


def test_reconciles_draft_and_state_into_artifact(tmp_path, session_factory):
    job_dir = tmp_path / "jobs" / "job-recon"
    # A confident pre-pass column (lives only in draft_mapping.json) ...
    _write_draft(
        job_dir,
        "job-recon",
        {
            "depth": {
                "proposed_slot": "depth",
                "confidence": 0.95,
                "status": "resolved",
            },
            "soil_horizon": {
                "proposed_slot": "soil_horizon",
                "confidence": 0.4,
                "status": "placeholder",
            },
        },
    )
    # ... and an agent resolution recorded in MappingState (DB only).
    state = _seed_state(
        "job-recon",
        [
            PlaceholderEntry(
                row_id="*",
                column="soil_horizon",
                proposed_slot="soil_horizon",
                value=_VALID_HORIZON,
                evidence=[{"source": "sheet", "quote_or_paraphrase": "A horizon"}],
                outcome=PlaceholderOutcome.RESOLVED,
            )
        ],
    )
    state.save_to_database_sync(session_factory)

    final = run_report_phase(state, job_dir, session_factory)

    artifact = _load(job_dir / MAPPED_OUTPUT_FILENAME)
    cols = artifact["columns"]
    # The confident pre-pass column is carried through (draft-only source).
    assert cols["depth"]["slot"] == "depth"
    assert cols["depth"]["resolved"] is True
    assert cols["depth"]["source"] == "prepass"
    # The agent resolution is reconciled in with its value + evidence.
    assert cols["soil_horizon"]["slot"] == "soil_horizon"
    assert cols["soil_horizon"]["value"] == _VALID_HORIZON
    assert cols["soil_horizon"]["resolved"] is True
    assert cols["soil_horizon"]["values"][0]["evidence"]
    assert final.placeholders[0].outcome == PlaceholderOutcome.RESOLVED


# -- validation pass path ------------------------------------------------------


def test_valid_value_passes_and_stays_resolved(tmp_path, session_factory):
    # Sanity: the real schema accepts the value we call "valid".
    assert validate_value("soil_horizon", _VALID_HORIZON, "SoilInterface").valid

    job_dir = tmp_path / "jobs" / "job-valid"
    _write_draft(job_dir, "job-valid", {})
    state = _seed_state(
        "job-valid",
        [
            PlaceholderEntry(
                row_id="*",
                column="soil_horizon",
                proposed_slot="soil_horizon",
                value=_VALID_HORIZON,
                outcome=PlaceholderOutcome.RESOLVED,
            )
        ],
    )
    state.save_to_database_sync(session_factory)

    final = run_report_phase(state, job_dir, session_factory)

    report = _load(job_dir / CURATION_REPORT_FILENAME)
    outcomes = {p["column"]: p["outcome"] for p in report["placeholders"]}
    assert outcomes["soil_horizon"] == "resolved"
    # And the artifact emits the validated value.
    artifact = _load(job_dir / MAPPED_OUTPUT_FILENAME)
    assert artifact["columns"]["soil_horizon"]["value"] == _VALID_HORIZON

    # The DB was updated to reflect the (unchanged) resolved outcome.
    reloaded = MappingState.load_from_database_sync("job-valid", session_factory)
    assert reloaded.placeholders[0].outcome == PlaceholderOutcome.RESOLVED
    assert final.placeholders[0].outcome == PlaceholderOutcome.RESOLVED


# -- validator_rejected path ---------------------------------------------------


def test_invalid_value_becomes_validator_rejected(tmp_path, session_factory):
    # Sanity: the real schema rejects this value for the enum slot.
    assert not validate_value(
        "soil_horizon", _INVALID_HORIZON, "SoilInterface"
    ).valid

    job_dir = tmp_path / "jobs" / "job-reject"
    _write_draft(job_dir, "job-reject", {})
    # The agent "resolved" the placeholder, but with a value the schema rejects.
    state = _seed_state(
        "job-reject",
        [
            PlaceholderEntry(
                row_id="*",
                column="soil_horizon",
                proposed_slot="soil_horizon",
                value=_INVALID_HORIZON,
                outcome=PlaceholderOutcome.RESOLVED,
            )
        ],
    )
    state.save_to_database_sync(session_factory)

    final = run_report_phase(state, job_dir, session_factory)

    # The outcome is flipped to validator_rejected in the finalized report ...
    report = _load(job_dir / CURATION_REPORT_FILENAME)
    entry = report["placeholders"][0]
    assert entry["outcome"] == "validator_rejected"
    assert entry["reason"]  # carries the validator's reason
    # ... and persisted to the DB ...
    reloaded = MappingState.load_from_database_sync("job-reject", session_factory)
    assert reloaded.placeholders[0].outcome == PlaceholderOutcome.VALIDATOR_REJECTED
    assert final.placeholders[0].outcome == PlaceholderOutcome.VALIDATOR_REJECTED
    # ... and the rejected value is NOT emitted as a confident cell.
    artifact = _load(job_dir / MAPPED_OUTPUT_FILENAME)
    assert artifact["columns"]["soil_horizon"]["resolved"] is False
    assert artifact["columns"]["soil_horizon"]["value"] is None


def test_left_placeholder_outcome_preserved(tmp_path, session_factory):
    job_dir = tmp_path / "jobs" / "job-left"
    _write_draft(job_dir, "job-left", {})
    state = _seed_state(
        "job-left",
        [
            PlaceholderEntry(
                row_id="*",
                column="sample_notes",
                proposed_slot=None,
                reason="free text; no schema slot",
                outcome=PlaceholderOutcome.LEFT_PLACEHOLDER,
            )
        ],
    )
    state.save_to_database_sync(session_factory)

    run_report_phase(state, job_dir, session_factory)

    report = _load(job_dir / CURATION_REPORT_FILENAME)
    entry = report["placeholders"][0]
    assert entry["outcome"] == "left_placeholder"
    assert entry["reason"] == "free text; no schema slot"
    # summary tallies each outcome.
    assert report["summary"]["left_placeholder"] == 1


# -- both files exist and are well-formed --------------------------------------


def test_both_outputs_exist_and_are_well_formed(tmp_path, session_factory):
    job_dir = tmp_path / "jobs" / "job-both"
    _write_draft(
        job_dir,
        "job-both",
        {
            "depth": {
                "proposed_slot": "depth",
                "confidence": 0.9,
                "status": "resolved",
            }
        },
    )
    state = _seed_state(
        "job-both",
        [
            PlaceholderEntry(
                row_id="*",
                column="soil_horizon",
                proposed_slot="soil_horizon",
                value=_VALID_HORIZON,
                outcome=PlaceholderOutcome.RESOLVED,
            )
        ],
    )
    state.save_to_database_sync(session_factory)

    run_report_phase(state, job_dir, session_factory)

    artifact_path = job_dir / MAPPED_OUTPUT_FILENAME
    report_path = job_dir / CURATION_REPORT_FILENAME
    assert artifact_path.is_file()
    assert report_path.is_file()

    artifact = _load(artifact_path)
    report = _load(report_path)
    assert artifact["job_id"] == "job-both"
    assert artifact["interface"] == "SoilInterface"
    assert report["job_id"] == "job-both"
    assert isinstance(report["placeholders"], list)

    # Every non-placeholder value in the artifact passes validate_value.
    interface = artifact["interface"]
    for col in artifact["columns"].values():
        if col["resolved"] and col["value"] is not None and col["slot"]:
            assert validate_value(col["slot"], col["value"], interface).valid
        for cell in col["values"]:
            assert validate_value(col["slot"], cell["value"], interface).valid


# -- freshness guard on the artifact write -------------------------------------


def test_artifact_write_is_fresh(tmp_path, session_factory):
    """The report phase guards the artifact (not the always-hot ledger)."""
    from harmonizer.orchestrator.loop import _is_fresh, _mtime_or_none

    job_dir = tmp_path / "jobs" / "job-fresh"
    _write_draft(job_dir, "job-fresh", {})
    state = _seed_state("job-fresh", [])
    state.save_to_database_sync(session_factory)

    artifact_path = job_dir / MAPPED_OUTPUT_FILENAME
    # Pre-existing stale artifact from a prior run.
    artifact_path.write_text("{}")
    baseline = _mtime_or_none(artifact_path)
    import os

    os.utime(artifact_path, (baseline - 5, baseline - 5))
    baseline = _mtime_or_none(artifact_path)

    run_report_phase(state, job_dir, session_factory)

    assert _is_fresh(artifact_path, baseline) is True
