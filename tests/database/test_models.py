"""Tests for the ORM models and enums (Phase 1)."""

from harmonizer.database.models import (
    Job,
    JobStatus,
    PlaceholderOutcome,
    PlaceholderRow,
)


def test_job_status_enum_values():
    assert {s.value for s in JobStatus} == {
        "pending",
        "running",
        "completed",
        "failed",
        "cancelled",
    }


def test_placeholder_outcome_enum_values():
    assert {o.value for o in PlaceholderOutcome} == {
        "pending",
        "resolved",
        "left_placeholder",
        "validator_rejected",
    }


def test_job_defaults_on_persist(session_factory):
    with session_factory() as session:
        session.add(Job(id="job-1", source_filename="sample.tsv"))
        session.commit()

    with session_factory() as session:
        job = session.get(Job, "job-1")
        assert job.status == JobStatus.PENDING
        assert job.max_iterations == 10
        assert job.current_iteration == 0
        assert job.error is None
        assert job.created_at is not None


def test_placeholder_relationship_and_cascade(session_factory):
    with session_factory() as session:
        job = Job(id="job-2", source_filename="s.tsv")
        job.placeholders.append(
            PlaceholderRow(
                row_id="0",
                column="env_broad_scale",
                proposed_slot="env_broad_scale",
                value="soil [ENVO:00001998]",
                confidence=0.9,
                evidence=[{"source": "runoak", "quote_or_paraphrase": "soil"}],
                outcome=PlaceholderOutcome.RESOLVED,
            )
        )
        session.add(job)
        session.commit()

    with session_factory() as session:
        job = session.get(Job, "job-2")
        assert len(job.placeholders) == 1
        row = job.placeholders[0]
        assert row.column == "env_broad_scale"
        assert row.evidence[0]["source"] == "runoak"
        # Deleting the job cascades to its placeholders.
        session.delete(job)
        session.commit()

    with session_factory() as session:
        assert session.get(Job, "job-2") is None
        assert session.query(PlaceholderRow).count() == 0
