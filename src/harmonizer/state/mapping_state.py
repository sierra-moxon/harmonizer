"""In-memory mapping state with synchronous DB load/save.

Mirrors OpenScientist's ``load_from_database_sync`` / ``save_to_database_sync``
pattern (pattern only; the code is authored here): the loop loads state, mutates
it in memory via the mutators, and persists it back. Placeholders are keyed by
``(row_id, column)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from harmonizer.database.models import (
    Job,
    JobStatus,
    PlaceholderOutcome,
    PlaceholderRow,
)
from harmonizer.database.session import get_session_factory


@dataclass
class PlaceholderEntry:
    """A single placeholder's working state (decoupled from the ORM row)."""

    row_id: str
    column: str
    proposed_slot: str | None = None
    value: str | None = None
    confidence: float | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    outcome: PlaceholderOutcome = PlaceholderOutcome.PENDING
    reason: str | None = None
    id: int | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.row_id, self.column)


@dataclass
class MappingState:
    """Working state for one mapping job, backed by the database."""

    job_id: str
    source_filename: str = ""
    interface_guess: str | None = None
    status: JobStatus = JobStatus.PENDING
    created_at: datetime | None = None
    max_iterations: int = 10
    current_iteration: int = 0
    error: str | None = None
    placeholders: list[PlaceholderEntry] = field(default_factory=list)

    # -- lookup ----------------------------------------------------------------

    def _find(self, row_id: str, column: str) -> PlaceholderEntry | None:
        for entry in self.placeholders:
            if entry.row_id == row_id and entry.column == column:
                return entry
        return None

    def remaining_placeholders(self) -> list[PlaceholderEntry]:
        """Return placeholders still awaiting resolution."""
        return [
            e for e in self.placeholders
            if e.outcome == PlaceholderOutcome.PENDING
        ]

    # -- mutators --------------------------------------------------------------

    def record_mapping(
        self,
        row_id: str,
        column: str,
        slot: str,
        value: str,
        evidence: list[dict[str, Any]] | None = None,
        confidence: float | None = None,
    ) -> PlaceholderEntry:
        """Record a resolved mapping for a placeholder, creating it if absent."""
        entry = self._find(row_id, column)
        if entry is None:
            entry = PlaceholderEntry(row_id=row_id, column=column)
            self.placeholders.append(entry)
        entry.proposed_slot = slot
        entry.value = value
        entry.evidence = list(evidence) if evidence else []
        entry.confidence = confidence
        entry.reason = None
        entry.outcome = PlaceholderOutcome.RESOLVED
        return entry

    def leave_placeholder(
        self,
        row_id: str,
        column: str,
        slot: str | None = None,
        reason: str = "",
    ) -> PlaceholderEntry:
        """Mark a placeholder as deliberately left unresolved."""
        entry = self._find(row_id, column)
        if entry is None:
            entry = PlaceholderEntry(row_id=row_id, column=column)
            self.placeholders.append(entry)
        if slot is not None:
            entry.proposed_slot = slot
        entry.reason = reason
        entry.outcome = PlaceholderOutcome.LEFT_PLACEHOLDER
        return entry

    # -- persistence -----------------------------------------------------------

    @classmethod
    def load_from_database_sync(
        cls,
        job_id: str,
        session_factory: sessionmaker[Session] | None = None,
    ) -> "MappingState":
        """Load a job and its placeholders into a fresh :class:`MappingState`."""
        factory = session_factory or get_session_factory()
        with factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ValueError(f"unknown job: {job_id!r}")
            placeholders = [
                PlaceholderEntry(
                    row_id=row.row_id,
                    column=row.column,
                    proposed_slot=row.proposed_slot,
                    value=row.value,
                    confidence=row.confidence,
                    evidence=list(row.evidence or []),
                    outcome=row.outcome,
                    reason=row.reason,
                    id=row.id,
                )
                for row in job.placeholders
            ]
            return cls(
                job_id=job.id,
                source_filename=job.source_filename,
                interface_guess=job.interface_guess,
                status=job.status,
                created_at=job.created_at,
                max_iterations=job.max_iterations,
                current_iteration=job.current_iteration,
                error=job.error,
                placeholders=placeholders,
            )

    def save_to_database_sync(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """Persist this state, creating the job row if it does not yet exist."""
        factory = session_factory or get_session_factory()
        with factory() as session:
            job = session.get(Job, self.job_id)
            if job is None:
                job = Job(id=self.job_id)
                session.add(job)

            job.source_filename = self.source_filename
            job.interface_guess = self.interface_guess
            job.status = self.status
            if self.created_at is not None:
                job.created_at = self.created_at
            job.max_iterations = self.max_iterations
            job.current_iteration = self.current_iteration
            job.error = self.error

            existing = {
                (row.row_id, row.column): row
                for row in session.scalars(
                    select(PlaceholderRow).where(
                        PlaceholderRow.job_id == self.job_id
                    )
                )
            }
            for entry in self.placeholders:
                row = existing.get(entry.key)
                if row is None:
                    row = PlaceholderRow(
                        job_id=self.job_id,
                        row_id=entry.row_id,
                        column=entry.column,
                    )
                    session.add(row)
                row.proposed_slot = entry.proposed_slot
                row.value = entry.value
                row.confidence = entry.confidence
                row.evidence = list(entry.evidence)
                row.outcome = entry.outcome
                row.reason = entry.reason

            session.commit()
