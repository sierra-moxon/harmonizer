"""Tests for the ledger tools (Phase 3): DB + curation_report round-trips."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harmonizer.database.models import PlaceholderOutcome
from harmonizer.job.setup import COLUMN_SCOPE, CURATION_REPORT_FILENAME, create_job
from harmonizer.state.mapping_state import MappingState
from harmonizer_tools import ledger_tools
from harmonizer_tools.state import ToolState, reset_state, set_state

_HEADERS = [
    "env_broad_scale",
    "env_local_scale",
    "env_medium",
    "depth",
    "cur_land_use",
    "sample_notes",  # noise -> placeholder
    "collector_name",  # noise -> placeholder
]
_ROWS = [
    ["terrestrial biome", "grassland", "soil", "10 cm", "conifers", "note", "A. C."],
    ["terrestrial biome", "forest", "soil", "20 cm", "conifers", "", "A. C."],
]


@pytest.fixture
def job(tmp_path, session_factory) -> ToolState:
    """A pre-passed job with an installed ToolState bound to the temp DB."""
    sheet = tmp_path / "soil_samples.tsv"
    lines = ["\t".join(_HEADERS)] + ["\t".join(r) for r in _ROWS]
    sheet.write_text("\n".join(lines) + "\n")

    jobs_root = tmp_path / "jobs"
    create_job(
        job_id="job-ledger",
        spreadsheet=sheet,
        jobs_root=jobs_root,
        session_factory=session_factory,
    )
    state = ToolState(
        job_id="job-ledger",
        job_dir=jobs_root / "job-ledger",
        session_factory=session_factory,
    )
    set_state(state)
    yield state
    reset_state()


def _report(state: ToolState) -> dict:
    return json.loads((state.job_dir / CURATION_REPORT_FILENAME).read_text())


def test_record_mapping_persists_and_updates_report(job):
    returned = ledger_tools.record_mapping(
        column="sample_notes",
        slot="samp_collect_device",
        value="corer",
        evidence=[{"source": "curation-rules", "quote_or_paraphrase": "device slot"}],
        confidence=0.9,
    )
    assert returned["outcome"] == "resolved"
    assert returned["value"] == "corer"

    # Database round-trips the resolution.
    reloaded = MappingState.load_from_database_sync("job-ledger", job.session_factory)
    entry = reloaded._find(COLUMN_SCOPE, "sample_notes")
    assert entry is not None
    assert entry.outcome == PlaceholderOutcome.RESOLVED
    assert entry.proposed_slot == "samp_collect_device"
    assert entry.value == "corer"
    assert entry.evidence[0]["source"] == "curation-rules"

    # curation_report.json reflects the resolved outcome + evidence.
    report = _report(job)
    row = next(p for p in report["placeholders"] if p["column"] == "sample_notes")
    assert row["outcome"] == "resolved"
    assert row["value"] == "corer"
    assert row["evidence"][0]["quote_or_paraphrase"] == "device slot"


def test_leave_placeholder_records_reason(job):
    returned = ledger_tools.leave_placeholder(
        column="collector_name",
        reason="no schema slot for collector identity",
    )
    assert returned["outcome"] == "left_placeholder"

    reloaded = MappingState.load_from_database_sync("job-ledger", job.session_factory)
    entry = reloaded._find(COLUMN_SCOPE, "collector_name")
    assert entry.outcome == PlaceholderOutcome.LEFT_PLACEHOLDER
    assert entry.reason == "no schema slot for collector identity"

    report = _report(job)
    row = next(p for p in report["placeholders"] if p["column"] == "collector_name")
    assert row["outcome"] == "left_placeholder"
    assert row["reason"] == "no schema slot for collector identity"


def test_value_level_placeholder_created_on_demand(job):
    # A row-scoped resolution the pre-pass never saw is created on the fly.
    ledger_tools.record_mapping(
        column="env_medium",
        slot="env_medium",
        value="soil [ENVO:00001998]",
        row="0",
        evidence=[{"source": "runoak", "quote_or_paraphrase": "ENVO:00001998 soil"}],
    )
    reloaded = MappingState.load_from_database_sync("job-ledger", job.session_factory)
    entry = reloaded._find("0", "env_medium")
    assert entry is not None
    assert entry.value == "soil [ENVO:00001998]"
    assert entry.outcome == PlaceholderOutcome.RESOLVED

    report = _report(job)
    keys = {(p["row_id"], p["column"]) for p in report["placeholders"]}
    assert ("0", "env_medium") in keys
