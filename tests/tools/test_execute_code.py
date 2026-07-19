"""Tests for the execute_code tool (Phase 3)."""

from __future__ import annotations

import pytest

from harmonizer_tools.execute_code import execute_code
from harmonizer_tools.state import DATA_DIRNAME, ToolState, reset_state, set_state


@pytest.fixture
def job(tmp_path):
    """A minimal job dir holding a sheet, with an installed ToolState."""
    job_dir = tmp_path / "job-exec"
    data_dir = job_dir / DATA_DIRNAME
    data_dir.mkdir(parents=True)
    (data_dir / "sheet.tsv").write_text("a\tb\n1\t2\n3\t4\n")
    set_state(ToolState(job_id="job-exec", job_dir=job_dir))
    yield job_dir
    reset_state()


def test_execute_code_sees_data_frame(job):
    result = execute_code("print(list(data.columns)); print(len(data))")
    assert result["error"] is None
    assert "['a', 'b']" in result["stdout"]
    assert "2" in result["stdout"]


def test_execute_code_captures_traceback(job):
    result = execute_code("raise ValueError('boom')")
    assert result["error"] is not None
    assert "ValueError: boom" in result["error"]
