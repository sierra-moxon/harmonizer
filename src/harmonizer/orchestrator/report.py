"""Report phase (Phase 7): emit the conformant artifact and the audit trail.

This module owns the *content* of the two job outputs; the loop
(:mod:`harmonizer.orchestrator.loop`) owns *control* and simply calls
:func:`run_report_phase` once, after the iteration loop, without surrendering
any loop ownership.

Two outputs are produced into the job directory:

* **The schema-conformant artifact** (``mapped_output.json``) — a filled,
  per-column mapping reconciled from **both** sources of truth:

  1. the confident column->slot mappings the deterministic pre-pass wrote to
     ``draft_mapping.json`` (resolved columns live *only* there; they are never
     persisted to the DB — see Phase 3 caveat #3), and
  2. the agent's resolutions recorded in :class:`MappingState` (column- and
     value-level, plus any evidence).

  Every non-placeholder value is validated with the Phase 0
  :func:`~harmonizer.schema.submission_schema.validate_value`. A value that
  fails validation is *not* emitted as a confident cell and its ledger entry is
  flipped to :data:`PlaceholderOutcome.VALIDATOR_REJECTED` (this is where schema
  validation feeds back into placeholder outcomes).

* **The curation report** (``curation_report.json``) — finalized from
  :class:`MappingState`: one entry per placeholder with its outcome
  (``resolved`` / ``left_placeholder`` / ``validator_rejected``) and evidence.

**Freshness guard.** The artifact write is protected by the same freshness
mechanic the loop uses per turn (:func:`harmonizer.orchestrator.loop._is_fresh`),
but pointed at the *real* schema-conformant artifact rather than the
always-hot ``curation_report.json``. Phase 6's guard proved only "the agent
touched the ledger"; here we prove "the report phase produced the finalized
artifact."
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from harmonizer.database.models import PlaceholderOutcome
from harmonizer.job.setup import (
    CURATION_REPORT_FILENAME,
    DRAFT_MAPPING_FILENAME,
    MAPPED_OUTPUT_FILENAME,
)
from harmonizer.schema.submission_schema import validate_value
from harmonizer.state.mapping_state import MappingState, PlaceholderEntry


def _load_draft_columns(job_dir: Path) -> dict[str, dict]:
    """Return the ``columns`` block of ``draft_mapping.json`` (``{}`` if absent)."""
    draft_path = job_dir / DRAFT_MAPPING_FILENAME
    try:
        payload = json.loads(draft_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    columns = payload.get("columns")
    return columns if isinstance(columns, dict) else {}


def _validate_entry(
    entry: PlaceholderEntry, interface: str | None
) -> tuple[bool, str]:
    """Validate a resolved entry's value against its slot.

    Returns ``(ok, reason)``. Entries without a concrete slot or value cannot be
    validated against the schema and are treated as passing (there is nothing to
    check). ``validate_value`` may raise :class:`ValueError` for an unknown
    slot/interface; that is surfaced as a rejection reason.
    """
    if not entry.proposed_slot or entry.value is None or entry.value == "":
        return True, ""
    try:
        result = validate_value(entry.proposed_slot, entry.value, interface)
    except ValueError as exc:
        return False, str(exc)
    return result.valid, result.reason


def _apply_validation(state: MappingState) -> None:
    """Flip resolved-but-invalid entries to ``validator_rejected`` in place.

    Iterates the ledger and re-validates every ``RESOLVED`` entry with a value.
    A value that fails :func:`validate_value` (or references an unknown
    slot/interface) has its outcome set to
    :data:`PlaceholderOutcome.VALIDATOR_REJECTED` and the validator's reason
    recorded, so the finalized report and the reconciled artifact agree.
    """
    for entry in state.placeholders:
        if entry.outcome != PlaceholderOutcome.RESOLVED:
            continue
        ok, reason = _validate_entry(entry, state.interface_guess)
        if not ok:
            entry.outcome = PlaceholderOutcome.VALIDATOR_REJECTED
            entry.reason = reason or "value rejected by schema validation"


def _build_artifact(state: MappingState, draft_columns: dict[str, dict]) -> dict:
    """Reconcile ``draft_mapping.json`` + ``MappingState`` into the artifact.

    The artifact is a per-column mapping. Confident pre-pass columns
    (``status == "resolved"`` in the draft, which live *only* on disk) seed the
    mapping with their slot; the agent's ledger resolutions layer on top,
    supplying slots/values/evidence and *overriding* the draft where they
    overlap. Only entries whose outcome is ``resolved`` after validation
    contribute a value; ``left_placeholder`` / ``validator_rejected`` columns are
    surfaced as unresolved so the artifact never carries an unvalidated value.
    """
    columns: dict[str, dict] = {}

    # 1) Seed from the confident pre-pass columns (draft_mapping.json only).
    #    These are slot-resolved with no per-cell value yet.
    for name, info in draft_columns.items():
        if info.get("status") == "resolved" and info.get("proposed_slot"):
            columns[name] = {
                "slot": info["proposed_slot"],
                "value": None,
                "values": [],
                "resolved": True,
                "source": "prepass",
                "confidence": info.get("confidence"),
                "outcome": None,
            }

    # 2) Layer the agent's ledger resolutions on top, keyed by column.
    for entry in state.placeholders:
        col = columns.setdefault(
            entry.column,
            {
                "slot": None,
                "value": None,
                "values": [],
                "resolved": False,
                "source": "agent",
                "confidence": None,
                "outcome": None,
            },
        )
        if entry.proposed_slot:
            col["slot"] = entry.proposed_slot
        if entry.confidence is not None:
            col["confidence"] = entry.confidence
        col["source"] = "agent" if col["source"] != "prepass" else "prepass+agent"
        col["outcome"] = entry.outcome.value

        if entry.outcome == PlaceholderOutcome.RESOLVED and entry.value not in (
            None,
            "",
        ):
            col["values"].append(
                {
                    "row_id": entry.row_id,
                    "value": entry.value,
                    "evidence": list(entry.evidence),
                }
            )
            if entry.row_id == "*":
                col["value"] = entry.value
            col["resolved"] = True
        else:
            # A refused / rejected / pending agent entry that supplies no value
            # marks the column unresolved unless a confident pre-pass slot
            # already resolved it AND the agent did not override the slot.
            if col["source"] == "agent":
                col["resolved"] = False

    return {
        "job_id": state.job_id,
        "interface": state.interface_guess,
        "columns": columns,
    }


def _report_entry(entry: PlaceholderEntry) -> dict:
    """Serialize a placeholder for the finalized ``curation_report.json``.

    Matches the ledger tools' serialization so on-disk shape stays stable.
    """
    return {
        "row_id": entry.row_id,
        "column": entry.column,
        "proposed_slot": entry.proposed_slot,
        "value": entry.value,
        "confidence": entry.confidence,
        "evidence": list(entry.evidence),
        "outcome": entry.outcome.value,
        "reason": entry.reason,
    }


def _build_report(state: MappingState) -> dict:
    """Build the finalized curation report payload from the ledger."""
    placeholders = [_report_entry(e) for e in state.placeholders]
    counts: dict[str, int] = {}
    for entry in state.placeholders:
        counts[entry.outcome.value] = counts.get(entry.outcome.value, 0) + 1
    return {
        "job_id": state.job_id,
        "interface": state.interface_guess,
        "summary": counts,
        "placeholders": placeholders,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")


def run_report_phase(
    state: MappingState,
    job_dir: str | Path,
    session_factory: sessionmaker[Session] | None = None,
) -> MappingState:
    """Produce both Phase 7 outputs for the job rooted at ``job_dir``.

    Signature matches the loop's Phase 6 report seam (``state``, ``job_dir``,
    ``session_factory``) so the loop integrates by calling this directly. The
    loop retains all control; this function only writes content.

    Steps:

    1. Reload the ledger from the DB so the freshest agent writes are seen, then
       re-validate every resolved value (:func:`_apply_validation`) — flipping
       schema-invalid resolutions to ``validator_rejected`` — and persist.
    2. Reconcile ``draft_mapping.json`` + :class:`MappingState` into the
       schema-conformant artifact and write it under a freshness guard.
    3. Finalize ``curation_report.json`` from the (post-validation) ledger.

    Returns the final :class:`MappingState`.
    """
    from harmonizer.orchestrator.loop import _is_fresh, _mtime_or_none

    job_dir = Path(job_dir)

    # 1) Reload the ledger (the MCP tools mutate the DB directly), then apply
    #    schema validation, feeding failures back as validator_rejected outcomes.
    state = MappingState.load_from_database_sync(state.job_id, session_factory)
    _apply_validation(state)
    state.save_to_database_sync(session_factory)

    # 2) Reconcile both sources into the schema-conformant artifact and write it
    #    under the freshness guard (the artifact, not the always-hot ledger).
    draft_columns = _load_draft_columns(job_dir)
    artifact = _build_artifact(state, draft_columns)
    artifact_path = job_dir / MAPPED_OUTPUT_FILENAME
    baseline = _mtime_or_none(artifact_path)
    _write_json(artifact_path, artifact)
    if not _is_fresh(artifact_path, baseline):
        # The write should always be fresh; guard defensively (e.g. a clock that
        # did not advance) by touching to guarantee a strictly-newer mtime.
        current = _mtime_or_none(artifact_path) or 0.0
        import os

        os.utime(artifact_path, (current + 1, current + 1))

    # 3) Finalize the curation report from the post-validation ledger.
    report_path = job_dir / CURATION_REPORT_FILENAME
    _write_json(report_path, _build_report(state))

    return state
