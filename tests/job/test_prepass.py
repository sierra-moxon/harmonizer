"""Tests for the deterministic pre-pass / job setup (Phase 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harmonizer.database.models import PlaceholderOutcome
from harmonizer.job.setup import (
    COLUMN_SCOPE,
    CURATION_INPUTS_FILENAME,
    CURATION_REPORT_FILENAME,
    DRAFT_MAPPING_FILENAME,
    create_job,
)
from harmonizer.state.mapping_state import MappingState

_HEADERS = [
    "env_broad_scale",
    "env_local_scale",
    "env_medium",
    "depth",
    "ph",
    "cur_land_use",  # soil-specific, biases the interface guess to Soil
    "sample_notes",  # noise -> placeholder
    "collector_name",  # noise -> placeholder
]
_ROWS = [
    [
        "terrestrial biome",
        "grassland",
        "soil",
        "10 cm",
        "6.5",
        "conifers",
        "near the fence",
        "A. Curator",
    ],
    [
        "terrestrial biome",
        "forest",
        "soil",
        "20 cm",
        "7.1",
        "conifers",
        "",
        "A. Curator",
    ],
]


@pytest.fixture
def sample_tsv(tmp_path) -> Path:
    path = tmp_path / "soil_samples.tsv"
    lines = ["\t".join(_HEADERS)]
    lines += ["\t".join(row) for row in _ROWS]
    path.write_text("\n".join(lines) + "\n")
    return path


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def test_create_job_produces_sidecars_and_state(sample_tsv, tmp_path, session_factory):
    jobs_root = tmp_path / "jobs"
    state = create_job(
        job_id="job-1",
        spreadsheet=sample_tsv,
        study_context="soil microbiome survey",
        jobs_root=jobs_root,
        session_factory=session_factory,
    )

    job_dir = jobs_root / "job-1"
    # The three sidecars and the copied upload all exist.
    assert (job_dir / DRAFT_MAPPING_FILENAME).is_file()
    assert (job_dir / CURATION_INPUTS_FILENAME).is_file()
    assert (job_dir / CURATION_REPORT_FILENAME).is_file()
    assert (job_dir / "data" / "soil_samples.tsv").is_file()
    assert (job_dir / "provenance").is_dir()

    # Returned state is populated and interface guessed.
    assert state.interface_guess == "SoilInterface"
    assert state.source_filename == "soil_samples.tsv"


def test_draft_mapping_resolves_and_flags_placeholders(sample_tsv, tmp_path, session_factory):
    jobs_root = tmp_path / "jobs"
    create_job("job-2", sample_tsv, jobs_root=jobs_root, session_factory=session_factory)

    draft = _load(jobs_root / "job-2" / DRAFT_MAPPING_FILENAME)
    assert draft["interface"] == "SoilInterface"
    cols = draft["columns"]
    assert cols["env_broad_scale"]["status"] == "resolved"
    assert cols["env_broad_scale"]["proposed_slot"] == "env_broad_scale"
    assert cols["depth"]["status"] == "resolved"
    # Noise columns are left as placeholders, not force-mapped.
    assert cols["sample_notes"]["status"] == "placeholder"
    assert cols["sample_notes"]["proposed_slot"] is None
    assert cols["collector_name"]["status"] == "placeholder"


def test_curation_inputs_capture_samples_and_context(sample_tsv, tmp_path, session_factory):
    jobs_root = tmp_path / "jobs"
    create_job(
        "job-3",
        sample_tsv,
        study_context="soil microbiome survey",
        jobs_root=jobs_root,
        session_factory=session_factory,
    )

    inputs = _load(jobs_root / "job-3" / CURATION_INPUTS_FILENAME)
    assert inputs["study_context"] == "soil microbiome survey"
    ph = inputs["columns"]["ph"]
    assert ph["samples"] == ["6.5", "7.1"]
    # Blank cells are skipped and duplicates de-duplicated.
    assert inputs["columns"]["collector_name"]["samples"] == ["A. Curator"]


def test_curation_report_skeleton_one_row_per_placeholder(sample_tsv, tmp_path, session_factory):
    jobs_root = tmp_path / "jobs"
    create_job("job-4", sample_tsv, jobs_root=jobs_root, session_factory=session_factory)

    report = _load(jobs_root / "job-4" / CURATION_REPORT_FILENAME)
    columns = {p["column"] for p in report["placeholders"]}
    assert columns == {"sample_notes", "collector_name"}
    for p in report["placeholders"]:
        assert p["outcome"] == "pending"
        assert p["row_id"] == COLUMN_SCOPE


def test_state_seeded_with_placeholders_and_round_trips(sample_tsv, tmp_path, session_factory):
    jobs_root = tmp_path / "jobs"
    create_job("job-5", sample_tsv, jobs_root=jobs_root, session_factory=session_factory)

    reloaded = MappingState.load_from_database_sync("job-5", session_factory)
    assert reloaded.interface_guess == "SoilInterface"
    remaining = reloaded.remaining_placeholders()
    assert {p.column for p in remaining} == {"sample_notes", "collector_name"}
    for p in remaining:
        assert p.outcome == PlaceholderOutcome.PENDING
        assert p.row_id == COLUMN_SCOPE


def test_missing_spreadsheet_raises(tmp_path, session_factory):
    with pytest.raises(FileNotFoundError):
        create_job(
            "job-6",
            tmp_path / "nope.tsv",
            jobs_root=tmp_path / "jobs",
            session_factory=session_factory,
        )
