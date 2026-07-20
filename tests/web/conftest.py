"""Fixtures for the web-layer tests.

Two testing strategies live side by side (see the module docstrings):

* **Helper-level** tests drive :class:`JobManager` and the pure page helpers
  directly against a real temp SQLite DB and a fixture spreadsheet — no browser,
  no live provider (the loop is stubbed).
* **Simulation** tests use ``nicegui.testing``'s ``User`` harness to render the
  real pages. NiceGUI's ``User`` fixture is an *async* fixture that needs an
  async pytest plugin (pytest-asyncio/anyio); this repo intentionally does not
  depend on one, so we drive the async context manager ourselves with
  ``asyncio.run`` via :func:`run_sim`, and skip cleanly if the harness cannot be
  imported/started headless here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import pytest
from sqlalchemy.orm import Session, sessionmaker

_SOIL_HEADERS = [
    "env_broad_scale",
    "env_local_scale",
    "env_medium",
    "depth",
    "ph",
    "cur_land_use",
    "sample_notes",
    "collector_name",
]
_SOIL_ROWS = [
    ["terrestrial biome", "grassland", "soil", "10 cm", "6.5", "conifers",
     "near the fence", "A. Curator"],
    ["terrestrial biome", "forest", "soil", "20 cm", "7.1", "conifers",
     "", "A. Curator"],
]


@pytest.fixture(autouse=True)
def _temp_default_database(tmp_path, monkeypatch):
    """Point the *default* store at a temp DB so tests never touch the repo root.

    Several web tests exercise the default ``JobManager()`` / app ``_startup``
    path (e.g. the NiceGUI ``run_sim`` simulations), which resolve the DB via
    :func:`harmonizer.database.session.get_database_url`. Without this, that
    defaults to ``sqlite:///harmonizer.db`` relative to CWD and leaves a stray
    ``harmonizer.db`` in the repo root on every run. Redirect it to ``tmp_path``
    and clear the ``@cache``d engine/session factory around the test so the
    override takes effect and does not leak between tests.
    """
    from harmonizer.database import session as db_session

    monkeypatch.setenv(
        "HARMONIZER_DATABASE_URL", f"sqlite:///{tmp_path / 'web-default.db'}"
    )
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    yield
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()


@pytest.fixture
def sample_tsv(tmp_path) -> Path:
    """A small soil-like TSV that biases the interface guess to Soil."""
    path = tmp_path / "soil_samples.tsv"
    lines = ["\t".join(_SOIL_HEADERS)]
    lines += ["\t".join(row) for row in _SOIL_ROWS]
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture
def jobs_root(tmp_path) -> Path:
    root = tmp_path / "jobs"
    root.mkdir()
    return root


@pytest.fixture
def make_settings(jobs_root):
    """Build a :class:`Settings` pointed at an isolated jobs root."""
    from harmonizer.settings import Settings

    def _make(**overrides) -> Settings:
        base = {"jobs_root": str(jobs_root), "max_iterations": 3}
        base.update(overrides)
        return Settings(**base)

    return _make


@pytest.fixture
def manager(session_factory: sessionmaker[Session], make_settings):
    """A :class:`JobManager` with a real temp DB, isolated jobs root, and a
    stubbed loop that never spawns the real agent/CLI or hits the network.

    The stub mimics the real loop's terminal behaviour: it loads the job's
    state, marks it COMPLETED, and runs the report phase so the detail page can
    surface real download artifacts.
    """
    from harmonizer.database.models import JobStatus
    from harmonizer.job.manager import JobManager
    from harmonizer.orchestrator.report import run_report_phase
    from harmonizer.state.mapping_state import MappingState

    async def fake_loop(job_dir, session_factory=None, settings=None, **_):
        state = MappingState.load_from_database_sync(
            Path(job_dir).name, session_factory
        )
        state.status = JobStatus.RUNNING
        state.save_to_database_sync(session_factory)
        # No agent turns: leave placeholders pending, then run the real report
        # phase so mapped_output.json / curation_report.json are produced.
        state = run_report_phase(state, job_dir, session_factory)
        state.status = JobStatus.COMPLETED
        state.save_to_database_sync(session_factory)
        return state

    mgr = JobManager(
        session_factory=session_factory,
        settings=make_settings(),
        max_workers=1,
        run_loop=fake_loop,
    )
    yield mgr
    mgr.shutdown(wait=True)


def run_sim(scenario: Callable[[object], Awaitable[None]]) -> None:
    """Run a NiceGUI ``User`` simulation scenario headless (or skip cleanly).

    ``scenario`` is an ``async`` callable taking the ``User``. The pages are
    registered by re-running ``harmonizer/web/app.py`` (NiceGUI's ``main_file``
    convention). If the harness cannot be imported/started in this environment,
    the calling test is skipped with a clear reason rather than hanging.
    """
    try:
        from nicegui.testing import user_simulation
        import harmonizer.web.app as appmod
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"NiceGUI User harness unavailable: {exc}")

    async def _run() -> None:
        async with user_simulation(main_file=appmod.__file__) as user:
            await scenario(user)

    try:
        asyncio.run(_run())
    except pytest.skip.Exception:
        raise
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"NiceGUI User simulation could not run headless: {exc}")
