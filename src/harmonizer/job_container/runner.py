"""Launch one container per mapping run (Phase 9).

:class:`JobContainerRunner` is the container-isolation seam wired into
:meth:`harmonizer.job.manager.JobManager._run_job_blocking`. When
``settings.use_container_isolation`` is set, the manager calls
:meth:`JobContainerRunner.launch` instead of running the loop in process.

Design: the **pure** config-building logic (image name, env, mounts, labels,
the ``docker run`` argv) lives in :meth:`build_spec` and returns a
:class:`ContainerSpec`, so it is unit-testable *without a running Docker
daemon*. The single side effect — actually invoking ``docker run`` — lives in
:meth:`launch`/:meth:`_run`, which tests stub out.

We shell out to the ``docker`` CLI via :mod:`subprocess` rather than adding the
``docker`` Python SDK, keeping the dependency set unchanged. The container runs
the orchestrator loop (``python -m harmonizer.orchestrator <job_dir>``) inside
the agent image, which ships harmonizer + the Claude Agent SDK; that image in
turn builds on the executor image so ``runoak`` + the prefetched ENVO /
NCBITaxon sqlite are available for offline ontology resolution.

Mirrors OpenScientist's ``job_container/runner.py`` (pattern only; authored
here), minus its structural-biology tooling.

DB / SQLite caveat
------------------
A per-job container writing to the same SQLite file the host web process reads
is workable but fragile (file locking over a bind mount). ``HARMONIZER_DATABASE_URL``
is passed through so the container and host agree on the store; for concurrent
jobs prefer Postgres. See the plan's open risk #5 and ``docker-compose.yml``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from harmonizer.database.session import get_database_url
from harmonizer.settings import Settings, get_settings

#: Default image the per-job container runs. Built by ``Dockerfile.agent`` (which
#: builds on the executor image, so ``runoak`` + prefetched ontologies are
#: present). Overridable via ``HARMONIZER_JOB_IMAGE``.
DEFAULT_JOB_IMAGE = "harmonizer-agent:latest"

#: Label key/prefix for cleanup: every per-job container is tagged
#: ``harmonizer.job_id=<id>`` (and ``harmonizer.managed=true``) so orphans can be
#: found and removed with ``docker ps --filter label=harmonizer.managed=true``.
LABEL_MANAGED = "harmonizer.managed"
LABEL_JOB_ID = "harmonizer.job_id"

#: Where the job directory is mounted *inside* the container. The job runs with
#: ``HARMONIZER_JOB_DIR`` pointing here so the MCP tools/agent resolve the same
#: files the host wrote (draft_mapping.json, sidecars, .claude/skills, ...).
CONTAINER_JOB_DIR = "/job"

#: Env var names the container's tools/agent consume (kept in one place).
JOB_ID_ENV = "HARMONIZER_JOB_ID"
JOB_DIR_ENV = "HARMONIZER_JOB_DIR"
JOBS_ROOT_ENV = "HARMONIZER_JOBS_ROOT"
DATABASE_URL_ENV = "HARMONIZER_DATABASE_URL"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"


@dataclass(frozen=True)
class ContainerSpec:
    """A fully-resolved, side-effect-free description of a per-job container.

    Everything needed to launch the container — and nothing that requires a
    daemon — so tests can assert image/env/mounts/labels and the exact
    ``docker run`` argv without Docker installed.
    """

    image: str
    job_id: str
    #: Absolute host path bind-mounted read-write into the container at
    #: :data:`CONTAINER_JOB_DIR`.
    host_job_dir: str
    container_job_dir: str
    env: dict[str, str]
    labels: dict[str, str]
    #: The command run inside the container (argv after the image name).
    command: list[str]
    network: str | None = None
    auto_remove: bool = True
    name: str | None = None
    #: Extra host paths to bind-mount, ``{host: container}`` (e.g. the DB dir).
    extra_mounts: dict[str, str] = field(default_factory=dict)

    def docker_args(self) -> list[str]:
        """Return the full ``docker`` argv for this spec (``["docker", "run", ...]``).

        Deterministic ordering so tests can assert on it. Secrets in ``env`` are
        passed by value here (that is what ``docker run -e K=V`` requires); the
        caller is responsible for not logging the result verbatim.
        """
        args: list[str] = ["docker", "run"]
        if self.auto_remove:
            args.append("--rm")
        if self.name:
            args += ["--name", self.name]
        if self.network:
            args += ["--network", self.network]
        for key, value in self.labels.items():
            args += ["--label", f"{key}={value}"]
        # Primary job-dir mount (read-write) plus any extras.
        args += ["-v", f"{self.host_job_dir}:{self.container_job_dir}"]
        for host, container in self.extra_mounts.items():
            args += ["-v", f"{host}:{container}"]
        for key, value in self.env.items():
            args += ["-e", f"{key}={value}"]
        args.append(self.image)
        args += self.command
        return args


class JobContainerRunner:
    """Build and run a per-job container. Thin, testable, daemon-free config."""

    def __init__(
        self,
        settings: Settings | None = None,
        image: str | None = None,
        network: str | None = None,
        auto_remove: bool = True,
    ) -> None:
        self._settings = settings or get_settings()
        self._image = image or DEFAULT_JOB_IMAGE
        self._network = network
        self._auto_remove = auto_remove

    # -- pure config building (unit-testable, no daemon) ----------------------

    def build_env(self, job_id: str, host_job_dir: Path) -> dict[str, str]:
        """Return the environment passed into the container.

        Includes the job binding (``HARMONIZER_JOB_ID``/``_JOB_DIR``), the jobs
        root, the database URL, and the provider credential when available. The
        job dir is expressed as the *in-container* path so the tools/agent
        resolve the mounted files.
        """
        env: dict[str, str] = {
            JOB_ID_ENV: job_id,
            JOB_DIR_ENV: CONTAINER_JOB_DIR,
            JOBS_ROOT_ENV: str(Path(CONTAINER_JOB_DIR).parent),
            DATABASE_URL_ENV: self._settings_database_url(),
        }
        api_key = self._settings.anthropic_api_key
        if api_key:
            env[ANTHROPIC_API_KEY_ENV] = api_key
        return env

    def _settings_database_url(self) -> str:
        """Resolve the DB URL to hand the container (env override or default)."""
        return get_database_url()

    def build_spec(self, job_id: str, job_dir: str | Path) -> ContainerSpec:
        """Construct the :class:`ContainerSpec` for ``job_id`` (no side effects).

        ``job_dir`` is the host path; it is bind-mounted read-write at
        :data:`CONTAINER_JOB_DIR` and the container command runs the orchestrator
        loop against the in-container path.
        """
        host_job_dir = str(Path(job_dir).resolve())
        labels = {
            LABEL_MANAGED: "true",
            LABEL_JOB_ID: job_id,
        }
        return ContainerSpec(
            image=self._image,
            job_id=job_id,
            host_job_dir=host_job_dir,
            container_job_dir=CONTAINER_JOB_DIR,
            env=self.build_env(job_id, Path(host_job_dir)),
            labels=labels,
            command=["python", "-m", "harmonizer.orchestrator", CONTAINER_JOB_DIR],
            network=self._network,
            auto_remove=self._auto_remove,
            name=f"harmonizer-job-{job_id}",
        )

    # -- side effect (stubbed in tests) ---------------------------------------

    def launch(self, job_id: str, job_dir: str | Path) -> subprocess.CompletedProcess:
        """Build the spec and run the per-job container to completion.

        Blocking: returns the :class:`~subprocess.CompletedProcess`. Raises
        :class:`~subprocess.CalledProcessError` if the container exits non-zero,
        so the JobManager's ``try/except`` marks the job FAILED. The pure
        :meth:`build_spec` does all the config work; this method only performs
        the ``docker run`` side effect via :meth:`_run`.
        """
        spec = self.build_spec(job_id, job_dir)
        return self._run(spec)

    def _run(self, spec: ContainerSpec) -> subprocess.CompletedProcess:
        """Invoke ``docker run`` for ``spec`` (the sole daemon-touching call)."""
        return subprocess.run(spec.docker_args(), check=True)
