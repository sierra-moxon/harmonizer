"""SQLAlchemy models for jobs and the placeholder ledger.

The schema is deliberately small: a :class:`Job` owns many
:class:`PlaceholderRow` records, one per cell/column the deterministic pre-pass
could not resolve. The iterative agent loop accretes outcomes into these rows.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class JobStatus(str, enum.Enum):
    """Lifecycle states for a mapping job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PlaceholderOutcome(str, enum.Enum):
    """Tracked outcome for each placeholder in the curation report."""

    PENDING = "pending"
    RESOLVED = "resolved"
    LEFT_PLACEHOLDER = "left_placeholder"
    VALIDATOR_REJECTED = "validator_rejected"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    """A single spreadsheet-to-schema mapping run."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_filename: Mapped[str] = mapped_column(String, nullable=False)
    interface_guess: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), nullable=False, default=JobStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    max_iterations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10
    )
    current_iteration: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    placeholders: Mapped[list["PlaceholderRow"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="PlaceholderRow.id",
    )


class PlaceholderRow(Base):
    """A single unresolved cell/column awaiting agent resolution."""

    __tablename__ = "placeholder_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id"), nullable=False, index=True
    )
    row_id: Mapped[str] = mapped_column(String, nullable=False)
    column: Mapped[str] = mapped_column(String, nullable=False)
    proposed_slot: Mapped[str | None] = mapped_column(String, nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    outcome: Mapped[PlaceholderOutcome] = mapped_column(
        Enum(PlaceholderOutcome),
        nullable=False,
        default=PlaceholderOutcome.PENDING,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[Job] = relationship(back_populates="placeholders")
