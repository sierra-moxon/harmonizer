"""Engine and session factory for the SQLite-backed store.

Defaults to a local SQLite file; override with the ``HARMONIZER_DATABASE_URL``
environment variable. Bootstrap tables with :func:`init_db` (no Alembic needed
for the single-node SQLite default; migrations can be added later).
"""

from __future__ import annotations

import os
from functools import cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from harmonizer.database.models import Base

_DEFAULT_DATABASE_URL = "sqlite:///harmonizer.db"


def get_database_url() -> str:
    """Return the configured database URL (env override or SQLite default)."""
    return os.environ.get("HARMONIZER_DATABASE_URL", _DEFAULT_DATABASE_URL)


@cache
def get_engine() -> Engine:
    """Return a process-wide cached engine for the configured URL."""
    return create_engine(get_database_url())


@cache
def get_session_factory() -> sessionmaker[Session]:
    """Return a cached session factory bound to the engine."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def init_db(engine: Engine | None = None) -> None:
    """Create all tables if they do not already exist."""
    Base.metadata.create_all(engine or get_engine())
