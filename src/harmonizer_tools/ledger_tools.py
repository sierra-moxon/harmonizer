"""Ledger tools: record the agent's per-placeholder decisions.

Each call loads the job's :class:`~harmonizer.state.mapping_state.MappingState`,
applies a mutator, persists it, and rewrites ``curation_report.json`` so the
on-disk ledger and the database stay in lock-step.

Placeholder identity follows the ``(row_id, column)`` model from Phases 1-2. A
column-scoped decision (the common case: "this whole column maps to this slot")
uses the default ``row="*"`` sentinel. A value-scoped decision (e.g. resolving a
single cell's ``"soil"`` to ``"soil [ENVO:00001998]"``) passes a concrete
``row`` id; the entry is created on demand if the pre-pass never saw it. This is
the settled contract for value-level placeholders: they are ledger entries with
a concrete ``row``, and the report gains ``value`` / ``evidence`` fields
alongside the Phase 2 skeleton.
"""

from __future__ import annotations

import json

from harmonizer.job.setup import COLUMN_SCOPE
from harmonizer.state.mapping_state import MappingState
from harmonizer_tools.server import mcp
from harmonizer_tools.state import ToolState, get_state


def _report_entry(entry) -> dict:
    """Serialize a :class:`PlaceholderEntry` for ``curation_report.json``."""
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


def _sync_curation_report(state: ToolState, mapping_state: MappingState) -> None:
    """Rewrite ``curation_report.json`` from the current ledger state.

    Preserves ``job_id``/``interface`` if a report already exists, and lists
    every placeholder (pending, resolved, or left) so the report reflects each
    outcome with its evidence.
    """
    path = state.curation_report_path
    payload: dict = {
        "job_id": mapping_state.job_id,
        "interface": mapping_state.interface_guess,
    }
    if path.is_file():
        existing = json.loads(path.read_text())
        payload["job_id"] = existing.get("job_id", payload["job_id"])
        payload["interface"] = existing.get("interface", payload["interface"])
    payload["placeholders"] = [
        _report_entry(entry) for entry in mapping_state.placeholders
    ]
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")


@mcp.tool()
def record_mapping(
    column: str,
    slot: str,
    value: str,
    evidence: list[dict[str, str]] | None = None,
    row: str = COLUMN_SCOPE,
    confidence: float | None = None,
) -> dict:
    """Record an evidence-backed resolution for a placeholder.

    Args:
        column: the spreadsheet column being resolved.
        slot: the submission-schema slot the column maps to.
        value: the resolved value (e.g. a ``"label [CURIE]"`` string).
        evidence: list of ``{"source": ..., "quote_or_paraphrase": ...}`` items.
        row: cell identifier for value-level resolutions; defaults to the
            column-scope sentinel ``"*"``.
        confidence: optional confidence score.

    Creates the placeholder if the pre-pass never recorded it, sets its outcome
    to ``resolved``, persists, and refreshes ``curation_report.json``.
    """
    state = get_state()
    mapping_state = MappingState.load_from_database_sync(
        state.job_id, state.session_factory
    )
    entry = mapping_state.record_mapping(
        row_id=row,
        column=column,
        slot=slot,
        value=value,
        evidence=evidence,
        confidence=confidence,
    )
    mapping_state.save_to_database_sync(state.session_factory)
    _sync_curation_report(state, mapping_state)
    return _report_entry(entry)


@mcp.tool()
def leave_placeholder(
    column: str,
    reason: str,
    slot: str | None = None,
    row: str = COLUMN_SCOPE,
) -> dict:
    """Deliberately leave a placeholder unresolved, recording why.

    Args:
        column: the spreadsheet column being left unresolved.
        reason: the agent's justification for refusing to resolve.
        slot: optional proposed slot to record alongside the refusal.
        row: cell identifier for value-level placeholders; defaults to ``"*"``.

    Sets the outcome to ``left_placeholder``, persists, and refreshes
    ``curation_report.json``.
    """
    state = get_state()
    mapping_state = MappingState.load_from_database_sync(
        state.job_id, state.session_factory
    )
    entry = mapping_state.leave_placeholder(
        row_id=row,
        column=column,
        slot=slot,
        reason=reason,
    )
    mapping_state.save_to_database_sync(state.session_factory)
    _sync_curation_report(state, mapping_state)
    return _report_entry(entry)
