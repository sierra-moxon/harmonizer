"""Deterministic pre-pass: turn an uploaded spreadsheet into a draft mapping.

The pre-pass is intentionally deterministic and conservative. It loads the
uploaded sheet, guesses the target interface and per-column candidate slots by
fuzzy-matching headers against the schema (Phase 0), and emits three sidecars:

* ``draft_mapping.json`` — the guessed interface plus, per column, the proposed
  slot, a match confidence, and a ``status`` of ``resolved`` or ``placeholder``.
* ``curation_inputs.json`` — per-column samples/headers plus the study context,
  the raw material the agent inspects.
* ``curation_report.json`` — the ledger skeleton, one entry per placeholder.

It also seeds a :class:`~harmonizer.state.mapping_state.MappingState` in the
database with a placeholder per unresolved column. Anything the pre-pass cannot
confidently map is left as a placeholder for the iterative agent to resolve or
explicitly refuse — the pre-pass never guesses beyond its confidence threshold.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session, sessionmaker

from harmonizer.database.session import init_db
from harmonizer.schema.submission_schema import get_slots, list_interfaces
from harmonizer.state.mapping_state import MappingState, PlaceholderEntry

DATA_DIRNAME = "data"
PROVENANCE_DIRNAME = "provenance"

DRAFT_MAPPING_FILENAME = "draft_mapping.json"
CURATION_INPUTS_FILENAME = "curation_inputs.json"
CURATION_REPORT_FILENAME = "curation_report.json"

#: The schema-conformant artifact produced by the Phase 7 report phase. It
#: reconciles the confident column->slot mappings from ``draft_mapping.json``
#: with the agent's resolutions in ``MappingState`` and is the artifact whose
#: write the report-phase freshness guard protects.
MAPPED_OUTPUT_FILENAME = "mapped_output.json"

#: ``row_id`` sentinel marking a column-scoped placeholder (applies to the whole
#: column rather than a single cell).
COLUMN_SCOPE = "*"

DEFAULT_JOBS_ROOT = "jobs"
DEFAULT_SAMPLE_SIZE = 5
DEFAULT_CONFIDENCE_THRESHOLD = 0.8

_TAB_SUFFIXES = {".tsv", ".tab"}
_EXCEL_SUFFIXES = {".xlsx", ".xls"}


@dataclass(frozen=True)
class ColumnMatch:
    """The best slot guess for a single spreadsheet column."""

    column: str
    proposed_slot: str | None
    confidence: float
    samples: list[str]

    @property
    def resolved(self) -> bool:
        return self.proposed_slot is not None


# -- spreadsheet loading -------------------------------------------------------


def _read_spreadsheet(path: Path) -> pd.DataFrame:
    """Load a TSV/CSV/XLSX into a string DataFrame (delimiter sniffed for CSV)."""
    suffix = path.suffix.lower()
    if suffix in _EXCEL_SUFFIXES:
        return pd.read_excel(path, dtype=str, engine="openpyxl").fillna("")
    if suffix in _TAB_SUFFIXES:
        sep: str | None = "\t"
    elif suffix == ".csv":
        sep = ","
    else:
        # Unknown extension: let pandas sniff the delimiter.
        sep = None
    return pd.read_csv(
        path,
        sep=sep,
        dtype=str,
        keep_default_na=False,
        engine="python" if sep is None else "c",
    )


# -- fuzzy matching ------------------------------------------------------------


def _normalize(text: str) -> str:
    """Lowercase and collapse non-alphanumeric runs to single spaces."""
    out = []
    prev_space = False
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
            prev_space = False
        elif not prev_space:
            out.append(" ")
            prev_space = True
    return "".join(out).strip()


def _slot_candidates(slot) -> list[str]:
    """Normalized strings a header could match: name, title, and aliases."""
    raw = [slot.name, slot.title, *(slot.aliases or [])]
    seen: dict[str, None] = {}
    for value in raw:
        if not value:
            continue
        norm = _normalize(str(value))
        if norm:
            seen.setdefault(norm, None)
    return list(seen)


def _similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _best_slot_for_header(header: str, slots) -> tuple[str | None, float]:
    """Return the best-matching slot name and its score for ``header``."""
    norm_header = _normalize(header)
    if not norm_header:
        return None, 0.0
    best_slot: str | None = None
    best_score = 0.0
    for slot in slots:
        for candidate in _slot_candidates(slot):
            score = _similarity(norm_header, candidate)
            if score > best_score:
                best_score = score
                best_slot = slot.name
    return best_slot, best_score


def guess_interface(
    headers: list[str],
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> str | None:
    """Pick the interface whose slots best cover ``headers``.

    Scored by the number of confidently-matched headers first, then by mean
    match score as a tiebreak. Returns ``None`` only when there are no
    interfaces or no headers.
    """
    if not headers:
        return None
    best_interface: str | None = None
    best_key: tuple[int, float] = (-1, -1.0)
    for interface in list_interfaces():
        slots = get_slots(interface)
        scores = [
            _best_slot_for_header(header, slots)[1] for header in headers
        ]
        confident = sum(1 for s in scores if s >= threshold)
        mean_score = sum(scores) / len(scores)
        key = (confident, mean_score)
        if key > best_key:
            best_key = key
            best_interface = interface
    return best_interface


def match_columns(
    df: pd.DataFrame,
    interface: str,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> list[ColumnMatch]:
    """Match each column to a slot in ``interface`` and collect value samples."""
    slots = get_slots(interface)
    matches: list[ColumnMatch] = []
    for column in df.columns:
        slot, score = _best_slot_for_header(str(column), slots)
        resolved = slot is not None and score >= threshold
        matches.append(
            ColumnMatch(
                column=str(column),
                proposed_slot=slot if resolved else None,
                confidence=round(score, 4),
                samples=_column_samples(df[column], sample_size),
            )
        )
    return matches


def _column_samples(series: pd.Series, sample_size: int) -> list[str]:
    values: list[str] = []
    for raw in series.tolist():
        text = "" if raw is None else str(raw).strip()
        if text and text not in values:
            values.append(text)
        if len(values) >= sample_size:
            break
    return values


# -- sidecar writers -----------------------------------------------------------


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")


def _draft_mapping_payload(
    job_id: str,
    source_filename: str,
    interface: str | None,
    matches: list[ColumnMatch],
) -> dict:
    return {
        "job_id": job_id,
        "source_filename": source_filename,
        "interface": interface,
        "columns": {
            m.column: {
                "proposed_slot": m.proposed_slot,
                "confidence": m.confidence,
                "status": "resolved" if m.resolved else "placeholder",
            }
            for m in matches
        },
    }


def _curation_inputs_payload(
    job_id: str,
    interface: str | None,
    study_context: str,
    matches: list[ColumnMatch],
) -> dict:
    return {
        "job_id": job_id,
        "interface": interface,
        "study_context": study_context,
        "columns": {
            m.column: {
                "header": m.column,
                "proposed_slot": m.proposed_slot,
                "confidence": m.confidence,
                "samples": m.samples,
            }
            for m in matches
        },
    }


def _curation_report_payload(
    job_id: str,
    interface: str | None,
    matches: list[ColumnMatch],
) -> dict:
    return {
        "job_id": job_id,
        "interface": interface,
        "placeholders": [
            {
                "row_id": COLUMN_SCOPE,
                "column": m.column,
                "proposed_slot": m.proposed_slot,
                "confidence": m.confidence,
                "outcome": "pending",
                "reason": None,
            }
            for m in matches
            if not m.resolved
        ],
    }


# -- entry point ---------------------------------------------------------------


def create_job(
    job_id: str,
    spreadsheet: str | Path,
    study_context: str = "",
    max_iterations: int = 10,
    jobs_root: str | Path | None = None,
    session_factory: sessionmaker[Session] | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> MappingState:
    """Run the deterministic pre-pass for ``spreadsheet`` and seed state.

    Creates ``<jobs_root>/<job_id>/{data,provenance}``, copies the upload into
    ``data/``, writes the three sidecars into the job directory, and persists a
    :class:`MappingState` whose placeholders are the columns the pre-pass could
    not confidently map. Returns the seeded (and saved) state.
    """
    spreadsheet = Path(spreadsheet)
    if not spreadsheet.is_file():
        raise FileNotFoundError(f"spreadsheet not found: {spreadsheet}")

    root = Path(
        jobs_root
        if jobs_root is not None
        else os.environ.get("HARMONIZER_JOBS_ROOT", DEFAULT_JOBS_ROOT)
    )
    job_dir = root / job_id
    data_dir = job_dir / DATA_DIRNAME
    data_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / PROVENANCE_DIRNAME).mkdir(parents=True, exist_ok=True)

    stored_upload = data_dir / spreadsheet.name
    shutil.copyfile(spreadsheet, stored_upload)

    df = _read_spreadsheet(stored_upload)
    headers = [str(c) for c in df.columns]
    interface = guess_interface(headers, confidence_threshold)
    matches = (
        match_columns(df, interface, confidence_threshold, sample_size)
        if interface is not None
        else []
    )

    _write_json(
        job_dir / DRAFT_MAPPING_FILENAME,
        _draft_mapping_payload(job_id, spreadsheet.name, interface, matches),
    )
    _write_json(
        job_dir / CURATION_INPUTS_FILENAME,
        _curation_inputs_payload(job_id, interface, study_context, matches),
    )
    _write_json(
        job_dir / CURATION_REPORT_FILENAME,
        _curation_report_payload(job_id, interface, matches),
    )

    if session_factory is None:
        init_db()

    state = MappingState(
        job_id=job_id,
        source_filename=spreadsheet.name,
        interface_guess=interface,
        max_iterations=max_iterations,
        placeholders=[
            PlaceholderEntry(
                row_id=COLUMN_SCOPE,
                column=m.column,
                proposed_slot=m.proposed_slot,
                confidence=m.confidence,
            )
            for m in matches
            if not m.resolved
        ],
    )
    state.save_to_database_sync(session_factory)
    return state


def main(argv: list[str] | None = None) -> int:
    """CLI: run the pre-pass on a spreadsheet (``just prepass F=<file>``)."""
    import argparse
    import uuid

    parser = argparse.ArgumentParser(description="Run the deterministic pre-pass.")
    parser.add_argument("spreadsheet", help="path to a TSV/CSV/XLSX upload")
    parser.add_argument("--job-id", default=None, help="job id (default: random)")
    parser.add_argument("--study-context", default="", help="free-text context")
    parser.add_argument("--jobs-root", default=None, help="jobs root directory")
    parser.add_argument("--max-iterations", type=int, default=10)
    args = parser.parse_args(argv)

    job_id = args.job_id or uuid.uuid4().hex[:12]
    state = create_job(
        job_id=job_id,
        spreadsheet=args.spreadsheet,
        study_context=args.study_context,
        max_iterations=args.max_iterations,
        jobs_root=args.jobs_root,
    )
    remaining = state.remaining_placeholders()
    print(f"job {job_id}: interface={state.interface_guess}")
    print(
        f"  {len(state.placeholders)} placeholder column(s); "
        f"{len(remaining)} awaiting resolution"
    )
    for entry in remaining:
        print(f"    - {entry.column} (confidence {entry.confidence})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
