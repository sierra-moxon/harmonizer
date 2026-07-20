"""Unit tests for host-path translation and Docker network resolution.

Both helpers are daemon-free by design: :func:`to_host_path` is pure, and
:func:`resolve_docker_network` takes injectable ``hostname_path`` /
``inspect_networks`` so the auto-detect path is exercised without Docker.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from harmonizer.job_container.utils import resolve_docker_network, to_host_path


class _Settings:
    def __init__(self, host_project_dir, container_app_dir="/app"):
        self.host_project_dir = host_project_dir
        self.container_app_dir = container_app_dir


# -- to_host_path -------------------------------------------------------------


def test_to_host_path_maps_app_tree_onto_host():
    cs = _Settings("/host/project", "/app")
    assert to_host_path(Path("/app/jobs/abc"), cs) == Path("/host/project/jobs/abc")


def test_to_host_path_noop_without_host_project_dir():
    cs = _Settings(None, "/app")
    assert to_host_path(Path("/app/jobs/abc"), cs) == Path("/app/jobs/abc")


def test_to_host_path_noop_when_outside_app_dir():
    cs = _Settings("/host/project", "/app")
    # A path not under container_app_dir is returned unchanged.
    assert to_host_path(Path("/tmp/elsewhere"), cs) == Path("/tmp/elsewhere")


# -- resolve_docker_network ---------------------------------------------------


def test_resolve_network_prefers_configured():
    # Configured wins; hostname/inspect are never consulted.
    def boom(_):
        raise AssertionError("should not inspect when configured")

    net = resolve_docker_network("explicit-net", inspect_networks=boom)
    assert net == "explicit-net"


def test_resolve_network_autodetects_non_bridge(tmp_path):
    hostname_file = tmp_path / "hostname"
    hostname_file.write_text("abc123\n")

    def fake_inspect(container):
        assert container == "abc123"
        return {"bridge": {}, "harmonizer_default": {}}

    net = resolve_docker_network(
        None, hostname_path=hostname_file, inspect_networks=fake_inspect
    )
    assert net == "harmonizer_default"


def test_resolve_network_falls_back_to_bridge_on_error(tmp_path):
    hostname_file = tmp_path / "hostname"
    hostname_file.write_text("abc123\n")

    def fake_inspect(_):
        raise subprocess.CalledProcessError(1, "docker inspect")

    net = resolve_docker_network(
        None, hostname_path=hostname_file, inspect_networks=fake_inspect
    )
    assert net == "bridge"


def test_resolve_network_falls_back_when_only_bridge(tmp_path):
    hostname_file = tmp_path / "hostname"
    hostname_file.write_text("abc123\n")
    net = resolve_docker_network(
        None,
        hostname_path=hostname_file,
        inspect_networks=lambda _: {"bridge": {}},
    )
    assert net == "bridge"


def test_resolve_network_falls_back_when_hostname_missing(tmp_path):
    missing = tmp_path / "does-not-exist"
    net = resolve_docker_network(
        None,
        hostname_path=missing,
        inspect_networks=lambda _: {"should_not_be_used": {}},
    )
    assert net == "bridge"
