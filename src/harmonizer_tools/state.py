"""Per-job tool state, bound from ``HARMONIZER_JOB_*`` environment variables.

The MCP subprocess is launched once per job; every tool call resolves its
context (which job, which directory, which sheet, which database) through a
single process-wide :class:`ToolState`. In production the state is built from
the environment (:meth:`ToolState.from_env`); tests inject a :class:`ToolState`
directly via :func:`set_state` so the tools round-trip against a temp DB and a
temp ``job_dir`` without touching the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session, sessionmaker

from harmonizer.job.setup import (
    CURATION_REPORT_FILENAME,
    DATA_DIRNAME,
)

#: Environment variables the subprocess reads to bind its per-job context.
JOB_ID_ENV = "HARMONIZER_JOB_ID"
JOB_DIR_ENV = "HARMONIZER_JOB_DIR"

_TAB_SUFFIXES = {".tsv", ".tab"}
_EXCEL_SUFFIXES = {".xlsx", ".xls"}


@dataclass
class ToolState:
    """Context for one mapping job, shared by every tool in the subprocess.

    ``session_factory`` is optional: when ``None`` the ledger tools fall back to
    the default (``HARMONIZER_DATABASE_URL`` or the local SQLite file), matching
    the database the pre-pass wrote to. Tests pass an isolated factory instead.
    """

    job_id: str
    job_dir: Path
    session_factory: sessionmaker[Session] | None = None

    @classmethod
    def from_env(cls) -> "ToolState":
        """Build a :class:`ToolState` from ``HARMONIZER_JOB_*`` env vars."""
        try:
            job_id = os.environ[JOB_ID_ENV]
            job_dir = os.environ[JOB_DIR_ENV]
        except KeyError as exc:  # pragma: no cover - defensive
            raise RuntimeError(
                f"{exc.args[0]} must be set to launch harmonizer_tools"
            ) from None
        return cls(job_id=job_id, job_dir=Path(job_dir))

    # -- filesystem helpers ----------------------------------------------------

    @property
    def data_dir(self) -> Path:
        return self.job_dir / DATA_DIRNAME

    @property
    def curation_report_path(self) -> Path:
        return self.job_dir / CURATION_REPORT_FILENAME

    def data_file(self) -> Path:
        """Return the single uploaded sheet stored under ``data/``.

        Raises :class:`FileNotFoundError` if the directory holds no sheet.
        """
        candidates = sorted(p for p in self.data_dir.iterdir() if p.is_file())
        if not candidates:
            raise FileNotFoundError(f"no data file in {self.data_dir}")
        return candidates[0]

    def load_dataframe(self) -> pd.DataFrame:
        """Load the job's sheet as a string DataFrame (delimiter sniffed)."""
        path = self.data_file()
        suffix = path.suffix.lower()
        if suffix in _EXCEL_SUFFIXES:
            return pd.read_excel(path, dtype=str, engine="openpyxl").fillna("")
        if suffix in _TAB_SUFFIXES:
            sep: str | None = "\t"
        elif suffix == ".csv":
            sep = ","
        else:
            sep = None
        return pd.read_csv(
            path,
            sep=sep,
            dtype=str,
            keep_default_na=False,
            engine="python" if sep is None else "c",
        )


# -- process-wide accessor -----------------------------------------------------

_current: ToolState | None = None


def set_state(state: ToolState) -> None:
    """Install ``state`` as the process-wide context (used by tests + bootstrap)."""
    global _current
    _current = state


def reset_state() -> None:
    """Clear the installed state (test teardown)."""
    global _current
    _current = None


def get_state() -> ToolState:
    """Return the installed state, lazily building one from the environment."""
    global _current
    if _current is None:
        _current = ToolState.from_env()
    return _current
