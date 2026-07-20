"""Unit tests for :class:`JobContainerRunner` — Phase 9 container isolation.

These tests exercise the **pure** config-building logic (image, env, mounts,
labels, the ``docker run`` argv) *without a Docker daemon*. The single side
effect (``docker run`` via subprocess) is stubbed. A gated integration smoke
test is skipped by default so the normal suite never needs Docker/network.
"""

from __future__ import annotations

import os

import pytest

from harmonizer.job_container.runner import (
    ANTHROPIC_API_KEY_ENV,
    CONTAINER_JOB_DIR,
    DATABASE_URL_ENV,
    DEFAULT_JOB_IMAGE,
    JOB_DIR_ENV,
    JOB_ID_ENV,
    JOBS_ROOT_ENV,
    LABEL_JOB_ID,
    LABEL_MANAGED,
    ContainerSpec,
    JobContainerRunner,
)
from harmonizer.settings import Settings


@pytest.fixture
def settings(monkeypatch) -> Settings:
    """Settings with a provider key and container isolation on.

    ``anthropic_api_key`` uses a ``validation_alias`` (``ANTHROPIC_API_KEY``), so
    it is populated from the environment rather than an init kwarg.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    return Settings(use_container_isolation=True, jobs_root="jobs")


# -- image / spec basics -------------------------------------------------------


def test_default_image_used(settings, tmp_path):
    runner = JobContainerRunner(settings)
    spec = runner.build_spec("job123", tmp_path)
    assert spec.image == DEFAULT_JOB_IMAGE


def test_image_override(settings, tmp_path):
    runner = JobContainerRunner(settings, image="custom-agent:2")
    spec = runner.build_spec("job123", tmp_path)
    assert spec.image == "custom-agent:2"


def test_spec_is_frozen(settings, tmp_path):
    spec = JobContainerRunner(settings).build_spec("j", tmp_path)
    assert isinstance(spec, ContainerSpec)
    with pytest.raises(Exception):
        spec.image = "other"  # type: ignore[misc]


# -- env dict includes the required vars --------------------------------------


def test_env_includes_required_job_binding(settings, tmp_path):
    env = JobContainerRunner(settings).build_env("job-abc", tmp_path)
    assert env[JOB_ID_ENV] == "job-abc"
    # The job dir passed to the container is the *in-container* mount path.
    assert env[JOB_DIR_ENV] == CONTAINER_JOB_DIR
    assert JOBS_ROOT_ENV in env
    assert DATABASE_URL_ENV in env


def test_env_includes_provider_key_when_set(settings, tmp_path):
    env = JobContainerRunner(settings).build_env("j", tmp_path)
    assert env[ANTHROPIC_API_KEY_ENV] == "sk-test-123"


def test_env_omits_provider_key_when_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HARMONIZER_ANTHROPIC_API_KEY", raising=False)
    s = Settings()
    env = JobContainerRunner(s).build_env("j", tmp_path)
    assert ANTHROPIC_API_KEY_ENV not in env


def test_env_database_url_follows_env_override(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HARMONIZER_DATABASE_URL", "sqlite:////data/harmonizer.db")
    env = JobContainerRunner(settings).build_env("j", tmp_path)
    assert env[DATABASE_URL_ENV] == "sqlite:////data/harmonizer.db"


# -- mounts include the job dir -----------------------------------------------


def test_mount_includes_resolved_job_dir(settings, tmp_path):
    spec = JobContainerRunner(settings).build_spec("j", tmp_path)
    assert spec.host_job_dir == str(tmp_path.resolve())
    assert spec.container_job_dir == CONTAINER_JOB_DIR


def test_docker_args_bind_mounts_job_dir(settings, tmp_path):
    args = JobContainerRunner(settings).build_spec("j", tmp_path).docker_args()
    expected = f"{tmp_path.resolve()}:{CONTAINER_JOB_DIR}"
    assert "-v" in args
    assert expected in args


# -- labels set ----------------------------------------------------------------


def test_labels_set_for_cleanup(settings, tmp_path):
    spec = JobContainerRunner(settings).build_spec("jobXYZ", tmp_path)
    assert spec.labels[LABEL_MANAGED] == "true"
    assert spec.labels[LABEL_JOB_ID] == "jobXYZ"


def test_docker_args_include_labels(settings, tmp_path):
    args = JobContainerRunner(settings).build_spec("jobXYZ", tmp_path).docker_args()
    assert f"{LABEL_MANAGED}=true" in args
    assert f"{LABEL_JOB_ID}=jobXYZ" in args


# -- auto-remove / network / command ------------------------------------------


def test_auto_remove_default_adds_rm_flag(settings, tmp_path):
    args = JobContainerRunner(settings).build_spec("j", tmp_path).docker_args()
    assert "--rm" in args


def test_auto_remove_off_omits_rm_flag(settings, tmp_path):
    runner = JobContainerRunner(settings, auto_remove=False)
    args = runner.build_spec("j", tmp_path).docker_args()
    assert "--rm" not in args


def test_network_flag_present_when_set(settings, tmp_path):
    runner = JobContainerRunner(settings, network="harmonizer-net")
    args = runner.build_spec("j", tmp_path).docker_args()
    assert "--network" in args
    assert "harmonizer-net" in args


def test_command_runs_orchestrator_loop(settings, tmp_path):
    spec = JobContainerRunner(settings).build_spec("j", tmp_path)
    assert spec.command == [
        "python",
        "-m",
        "harmonizer.orchestrator",
        CONTAINER_JOB_DIR,
    ]


def test_docker_args_start_with_docker_run_and_end_with_image_then_command(
    settings, tmp_path
):
    args = JobContainerRunner(settings).build_spec("j", tmp_path).docker_args()
    assert args[:2] == ["docker", "run"]
    image_idx = args.index(DEFAULT_JOB_IMAGE)
    # Everything after the image is the container command.
    assert args[image_idx + 1 :] == [
        "python",
        "-m",
        "harmonizer.orchestrator",
        CONTAINER_JOB_DIR,
    ]


def test_env_rendered_as_e_flags(settings, tmp_path):
    args = JobContainerRunner(settings).build_spec("job-abc", tmp_path).docker_args()
    assert "-e" in args
    assert f"{JOB_ID_ENV}=job-abc" in args


# -- side effect: launch() calls _run() without touching a real daemon --------


def test_launch_delegates_to_run_with_built_spec(settings, tmp_path, monkeypatch):
    # A networked (non-SQLite) store is required for isolation; the SQLite
    # guardrail is covered separately below. An explicit network short-circuits
    # auto-detection so the test never shells out to `docker`.
    monkeypatch.setenv(
        "HARMONIZER_DATABASE_URL",
        "postgresql+psycopg://harmonizer:harmonizer@db:5432/harmonizer",
    )
    runner = JobContainerRunner(settings, network="test-net")
    captured = {}

    def fake_run(spec):
        captured["spec"] = spec
        return "ran"

    monkeypatch.setattr(runner, "_run", fake_run)
    result = runner.launch("job-1", tmp_path)

    assert result == "ran"
    assert captured["spec"].job_id == "job-1"
    assert captured["spec"].image == DEFAULT_JOB_IMAGE
    assert captured["spec"].host_job_dir == str(tmp_path.resolve())
    assert captured["spec"].network == "test-net"


def test_launch_resolves_network_when_unset(settings, tmp_path, monkeypatch):
    """With no explicit network, launch() resolves one and puts it on the spec."""
    import harmonizer.job_container.runner as runner_mod

    monkeypatch.setenv(
        "HARMONIZER_DATABASE_URL",
        "postgresql+psycopg://harmonizer:harmonizer@db:5432/harmonizer",
    )
    monkeypatch.setattr(
        runner_mod, "resolve_docker_network", lambda *a, **k: "harmonizer-net"
    )
    runner = JobContainerRunner(settings)
    captured = {}
    monkeypatch.setattr(
        runner, "_run", lambda spec: captured.setdefault("spec", spec)
    )
    runner.launch("job-1", tmp_path)
    assert captured["spec"].network == "harmonizer-net"


def test_build_spec_translates_host_path(tmp_path, monkeypatch):
    """When host_project_dir is set, the bind-mount source is the host path."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings(
        host_project_dir="/host/project",
        container_app_dir="/app",
    )
    spec = JobContainerRunner(s).build_spec("j", "/app/jobs/j")
    assert spec.host_job_dir == "/host/project/jobs/j"


def test_launch_rejects_sqlite_store(settings, tmp_path, monkeypatch):
    """Isolation + a SQLite URL must fail fast (writes would never reach host)."""
    monkeypatch.setenv("HARMONIZER_DATABASE_URL", "sqlite:////data/harmonizer.db")
    runner = JobContainerRunner(settings)
    monkeypatch.setattr(runner, "_run", lambda spec: "should-not-run")
    with pytest.raises(RuntimeError, match="SQLite"):
        runner.launch("job-1", tmp_path)


def test_job_image_from_settings_used(tmp_path, monkeypatch):
    """``HARMONIZER_JOB_IMAGE`` (via Settings.job_image) drives the image."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings(job_image="harmonizer-agent:custom")
    spec = JobContainerRunner(s).build_spec("j", tmp_path)
    assert spec.image == "harmonizer-agent:custom"


def test_run_invokes_subprocess_with_docker_args(settings, tmp_path, monkeypatch):
    import harmonizer.job_container.runner as runner_mod

    calls = {}

    def fake_subprocess_run(argv, check):
        calls["argv"] = argv
        calls["check"] = check
        return "completed"

    monkeypatch.setattr(runner_mod.subprocess, "run", fake_subprocess_run)
    runner = JobContainerRunner(settings)
    spec = runner.build_spec("j", tmp_path)
    result = runner._run(spec)

    assert result == "completed"
    assert calls["check"] is True
    assert calls["argv"] == spec.docker_args()


# -- gated integration smoke test (skipped by default; never hits network) ----


@pytest.mark.skipif(
    os.environ.get("HARMONIZER_DOCKER_SMOKE") != "1",
    reason="Docker smoke test is gated (set HARMONIZER_DOCKER_SMOKE=1 to run).",
)
def test_docker_smoke_runoak_offline(tmp_path):  # pragma: no cover - manual gate
    """Real per-job container resolves ENVO offline (manual, gated).

    Requires a built ``harmonizer-agent:latest`` image and a Docker daemon.
    Runs ``runoak info ENVO:00001998`` inside the image to prove the prefetched
    ontology cache works without network.
    """
    import subprocess

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            DEFAULT_JOB_IMAGE,
            "runoak",
            "-i",
            "sqlite:obo:envo",
            "info",
            "ENVO:00001998",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ENVO:00001998" in result.stdout
