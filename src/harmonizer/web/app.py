"""NiceGUI + FastAPI app entry for the Harmonizer web UI.

Run locally with ``python -m harmonizer.web`` (``just web``). Single-user, local
by default — no OAuth/multi-tenancy (deferred).

Pages (registered on import so ``nicegui.testing`` can drive them):

* ``/`` — job list with status badges.
* ``/new`` — upload a TSV/CSV/XLSX + optional study context + ``max_iterations``,
  which runs the deterministic pre-pass (:meth:`JobManager.create_job`), submits
  the iterative loop to the worker pool (:meth:`JobManager.submit_job`), and
  redirects to the job detail page.
* ``/job/<id>`` — live status via ``@ui.refreshable`` + ``ui.timer`` polling the
  :class:`~harmonizer.state.mapping_state.MappingState` / ``Job`` row; shows
  remaining vs. resolved placeholder counts and, on completion, download links
  for ``mapped_output.json`` and ``curation_report.json``.
* ``/schema`` — browse interfaces and their slots from the schema module.

The pages share a single process-wide :class:`JobManager` (thread pool + one
session factory). ``init_db`` runs on startup.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from nicegui import app, ui

from harmonizer.database.models import Job
from harmonizer.database.session import init_db
from harmonizer.job.manager import JobManager
from harmonizer.schema.submission_schema import get_slots, list_interfaces
from harmonizer.state.mapping_state import MappingState
from harmonizer.web.helpers import (
    Progress,
    compute_progress,
    is_terminal,
    resolve_downloads,
    status_color,
)

#: Poll interval (seconds) for the live job-detail page.
POLL_SECONDS = 1.5

_manager: JobManager | None = None


def get_manager() -> JobManager:
    """Return the process-wide :class:`JobManager`, creating it on first use."""
    global _manager
    if _manager is None:
        _manager = JobManager()
    return _manager


def set_manager(manager: JobManager | None) -> None:
    """Override the process-wide manager (used by tests to inject a stub)."""
    global _manager
    _manager = manager


# -- shared chrome -------------------------------------------------------------


def _header(title: str) -> None:
    with ui.header().classes("items-center justify-between"):
        ui.link("Harmonizer", "/").classes(
            "text-white text-lg font-bold no-underline"
        )
        with ui.row():
            ui.link("Jobs", "/").classes("text-white no-underline")
            ui.link("New job", "/new").classes("text-white no-underline")
            ui.link("Schema", "/schema").classes("text-white no-underline")
    ui.label(title).classes("text-2xl font-bold q-mt-md")


# -- / (job list) --------------------------------------------------------------


@ui.page("/")
def index_page() -> None:
    _header("Jobs")
    manager = get_manager()

    @ui.refreshable
    def job_table() -> None:
        jobs = manager.list_jobs()
        if not jobs:
            ui.label("No jobs yet. Create one from “New job”.").classes(
                "text-grey"
            )
            return
        with ui.list().props("bordered separator").classes("w-full"):
            for job in jobs:
                _job_row(job)

    job_table()
    ui.timer(POLL_SECONDS, job_table.refresh)


def _job_row(job: Job) -> None:
    with ui.item(on_click=lambda job_id=job.id: ui.navigate.to(f"/job/{job_id}")):
        with ui.item_section():
            ui.item_label(job.source_filename or job.id)
            ui.item_label(f"id {job.id}").props("caption")
        with ui.item_section().props("side"):
            ui.badge(job.status.value, color=status_color(job.status))


# -- /new (upload → create + submit) ------------------------------------------


@ui.page("/new")
def new_job_page() -> None:
    _header("New job")
    manager = get_manager()

    # Holds the streamed upload until the user submits the form.
    pending: dict[str, str] = {}

    study_context = (
        ui.textarea(
            label="Study context (optional)",
            placeholder="Free-text context to guide slot/value resolution…",
        )
        .props("outlined autogrow")
        .classes("w-full")
    )
    max_iterations = (
        ui.number(label="Max iterations", value=10, min=1, max=50, format="%d")
        .classes("w-40")
    )

    def _on_upload(event) -> None:
        # Stream the upload to a temp file; created here, consumed on submit.
        suffix = Path(event.name).suffix or ".tsv"
        fd, tmp = tempfile.mkstemp(prefix="harmonizer-upload-", suffix=suffix)
        with open(fd, "wb") as handle:
            handle.write(event.content.read())
        pending["path"] = tmp
        pending["name"] = event.name
        ui.notify(f"Uploaded {event.name}")

    ui.upload(on_upload=_on_upload, auto_upload=True).props(
        'accept=".tsv,.csv,.xlsx,.xls,.tab"'
    ).classes("w-full")

    def _submit() -> None:
        if "path" not in pending:
            ui.notify("Please upload a spreadsheet first.", type="warning")
            return
        job_id = create_and_submit(
            manager,
            spreadsheet=pending["path"],
            study_context=study_context.value or "",
            max_iterations=int(max_iterations.value or 10),
            original_name=pending.get("name"),
        )
        ui.navigate.to(f"/job/{job_id}")

    ui.button("Create job", on_click=_submit).props("color=primary")


def create_and_submit(
    manager: JobManager,
    spreadsheet: str | Path,
    study_context: str,
    max_iterations: int,
    original_name: str | None = None,
) -> str:
    """Run the pre-pass then submit the loop; return the new job id.

    Factored out of the page so tests can exercise create→submit without the
    browser. The uploaded temp file keeps its original name inside the job's
    ``data/`` dir by copying it to a same-named path first when possible.
    """
    spreadsheet = Path(spreadsheet)
    if original_name:
        # Preserve the user's filename inside the job dir (setup copies by name).
        renamed = spreadsheet.with_name(original_name)
        if renamed != spreadsheet:
            spreadsheet.replace(renamed)
            spreadsheet = renamed
    job_id = manager.create_job(
        spreadsheet=spreadsheet,
        study_context=study_context,
        max_iterations=max_iterations,
    )
    manager.submit_job(job_id)
    return job_id


# -- /job/<id> (live status + downloads) --------------------------------------


@ui.page("/job/{job_id}")
def job_detail_page(job_id: str) -> None:
    _header(f"Job {job_id}")
    manager = get_manager()

    @ui.refreshable
    def detail() -> None:
        render_job_detail(manager, job_id)

    detail()
    ui.timer(POLL_SECONDS, detail.refresh)


def render_job_detail(manager: JobManager, job_id: str) -> None:
    """Render the current state of one job (called on every poll tick)."""
    job = manager.get_job(job_id)
    if job is None:
        ui.label(f"Unknown job: {job_id}").classes("text-red")
        return

    with ui.row().classes("items-center gap-2"):
        ui.label(job.source_filename or job.id).classes("text-lg font-bold")
        ui.badge(job.status.value, color=status_color(job.status))
    if job.interface_guess:
        ui.label(f"Interface: {job.interface_guess}").classes("text-grey")

    try:
        state = MappingState.load_from_database_sync(
            job_id, manager._session_factory
        )
    except ValueError:
        return
    progress = compute_progress(state)
    _render_progress(progress)

    if job.error:
        ui.label(f"Error: {job.error}").classes("text-red q-mt-sm")

    if is_terminal(job.status):
        _render_downloads(manager.job_dir(job_id))


def _render_progress(progress: Progress) -> None:
    ui.linear_progress(value=progress.fraction, show_value=False).classes(
        "q-mt-sm"
    )
    with ui.row().classes("gap-4 q-mt-sm"):
        ui.label(f"remaining: {progress.remaining}")
        ui.label(f"resolved: {progress.resolved}")
        ui.label(f"left placeholder: {progress.left_placeholder}")
        ui.label(f"validator rejected: {progress.validator_rejected}")
        ui.label(f"total: {progress.total}")


def _render_downloads(job_dir: Path) -> None:
    downloads = resolve_downloads(job_dir)
    if not downloads:
        ui.label("No artifacts were produced.").classes("text-grey q-mt-md")
        return
    ui.label("Downloads").classes("text-lg font-bold q-mt-md")
    with ui.row().classes("gap-2"):
        for dl in downloads:
            ui.button(
                dl.label,
                on_click=lambda d=dl: ui.download.file(d.path, d.filename),
            ).props("outline")


# -- /schema (browse interfaces & slots) --------------------------------------


@ui.page("/schema")
def schema_page() -> None:
    _header("Schema")
    interfaces = list_interfaces()
    if not interfaces:
        ui.label("No interfaces found in the schema.").classes("text-grey")
        return
    for interface in interfaces:
        with ui.expansion(interface).classes("w-full"):
            try:
                slots = get_slots(interface)
            except ValueError:
                ui.label("(could not load slots)").classes("text-red")
                continue
            for slot in slots:
                ui.label(slot.name).props("dense")


# -- startup / run -------------------------------------------------------------


@app.on_startup
def _startup() -> None:
    init_db()
    get_manager().ensure_schema()


@app.on_shutdown
def _shutdown() -> None:
    if _manager is not None:
        _manager.shutdown(wait=False)


def run(host: str = "127.0.0.1", port: int = 8080, reload: bool = False) -> None:
    """Start the NiceGUI server (blocking). Backs ``python -m harmonizer.web``."""
    ui.run(host=host, port=port, reload=reload, title="Harmonizer")


# NiceGUI convention: a runnable main file calls ``ui.run()`` behind a
# multiprocessing-friendly guard. This is what ``nicegui.testing`` re-executes
# (``runpy``) to register the pages for a ``User`` simulation, and what a plain
# ``python src/harmonizer/web/app.py`` would use. The ``__main__.py`` module is
# the argparse-driven CLI entry (``python -m harmonizer.web``).
if __name__ in {"__main__", "__mp_main__"}:
    run()
