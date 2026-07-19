"""Pure UI helpers, separated from NiceGUI so they are unit-testable.

Nothing in this module touches NiceGUI; the page modules import these to keep
rendering logic (status colours, progress counts, download-path resolution)
free of the browser runtime and covered by plain unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from harmonizer.database.models import JobStatus, PlaceholderOutcome
from harmonizer.job.setup import (
    CURATION_REPORT_FILENAME,
    MAPPED_OUTPUT_FILENAME,
)
from harmonizer.state.mapping_state import MappingState

#: Quasar/NiceGUI badge colour per job status.
_STATUS_COLORS: dict[JobStatus, str] = {
    JobStatus.PENDING: "grey",
    JobStatus.RUNNING: "blue",
    JobStatus.COMPLETED: "green",
    JobStatus.FAILED: "red",
    JobStatus.CANCELLED: "orange",
}


def status_color(status: JobStatus) -> str:
    """Return the badge colour for a :class:`JobStatus` (grey if unknown)."""
    return _STATUS_COLORS.get(status, "grey")


def is_terminal(status: JobStatus) -> bool:
    """Return ``True`` when the job will not change status any further."""
    return status in (
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    )


@dataclass(frozen=True)
class Progress:
    """A snapshot of placeholder progress for the detail page."""

    total: int
    remaining: int
    resolved: int
    left_placeholder: int
    validator_rejected: int

    @property
    def done(self) -> int:
        """Placeholders that reached a terminal outcome (not pending)."""
        return self.total - self.remaining

    @property
    def fraction(self) -> float:
        """Completion fraction in ``[0, 1]`` (1.0 when there is nothing to do)."""
        if self.total == 0:
            return 1.0
        return self.done / self.total


def compute_progress(state: MappingState) -> Progress:
    """Compute placeholder progress counts from a live :class:`MappingState`.

    ``remaining`` uses the state's own ``PENDING`` filter; the outcome counts are
    tallied directly so the detail page can show resolved vs. refused vs.
    validator-rejected without waiting for the finalized report.
    """
    total = len(state.placeholders)
    remaining = len(state.remaining_placeholders())
    resolved = sum(
        1
        for e in state.placeholders
        if e.outcome == PlaceholderOutcome.RESOLVED
    )
    left = sum(
        1
        for e in state.placeholders
        if e.outcome == PlaceholderOutcome.LEFT_PLACEHOLDER
    )
    rejected = sum(
        1
        for e in state.placeholders
        if e.outcome == PlaceholderOutcome.VALIDATOR_REJECTED
    )
    return Progress(
        total=total,
        remaining=remaining,
        resolved=resolved,
        left_placeholder=left,
        validator_rejected=rejected,
    )


@dataclass(frozen=True)
class Download:
    """A downloadable artifact resolved to an on-disk path."""

    label: str
    filename: str
    path: Path

    @property
    def exists(self) -> bool:
        return self.path.is_file()


def resolve_downloads(job_dir: str | Path) -> list[Download]:
    """Return the two result artifacts as :class:`Download`s (existing only).

    The schema-conformant artifact (``mapped_output.json``) and the curation
    report (``curation_report.json``) are only offered once they exist on disk in
    ``job_dir``, so a link is never dangling.
    """
    job_dir = Path(job_dir)
    candidates = [
        Download(
            label="Mapped output (schema-conformant)",
            filename=MAPPED_OUTPUT_FILENAME,
            path=job_dir / MAPPED_OUTPUT_FILENAME,
        ),
        Download(
            label="Curation report",
            filename=CURATION_REPORT_FILENAME,
            path=job_dir / CURATION_REPORT_FILENAME,
        ),
    ]
    return [d for d in candidates if d.exists]
