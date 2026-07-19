"""Shared test fixtures."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from harmonizer.database.models import Base


@pytest.fixture
def session_factory(tmp_path) -> sessionmaker[Session]:
    """An isolated SQLite database (temp file) with tables created."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
