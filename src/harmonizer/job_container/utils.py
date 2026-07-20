"""Path translation and Docker network resolution for sibling job containers.

Mirrors OpenScientist's ``job_container/utils.py`` (pattern only; authored here),
but resolves the network via the ``docker`` CLI rather than the Docker SDK so the
dependency set stays unchanged (see ``runner.py`` for that rationale).

Both helpers matter only on the container-isolation path, where the web process
launches per-job containers through the mounted Docker socket
(docker-out-of-docker):

* :func:`to_host_path` — bind-mount sources handed to the *host* daemon must be
  *host* paths, not the web container's internal paths. It maps a
  ``container_app_dir``-rooted path back onto ``host_project_dir``.
* :func:`resolve_docker_network` — sibling containers must join the web
  container's own (compose) network to reach the ``postgres`` service by
  hostname; this auto-detects that network.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Callable, Protocol

logger = logging.getLogger(__name__)


class HostPathSettings(Protocol):
    """Minimal settings interface required by :func:`to_host_path`."""

    host_project_dir: str | None
    container_app_dir: str


def to_host_path(path: Path, cs: HostPathSettings) -> Path:
    """Translate a container-internal path to its Docker-host path.

    Returns ``path`` unchanged when ``host_project_dir`` is unset (bare
    ``docker run`` / local dev) or when ``path`` is not under
    ``container_app_dir``.
    """
    if not cs.host_project_dir:
        return path

    container_app_dir = Path(cs.container_app_dir)
    host_project_dir = Path(cs.host_project_dir)

    try:
        relative = path.relative_to(container_app_dir)
        return host_project_dir / relative
    except ValueError:
        return path


def _docker_inspect_networks(container: str) -> dict:
    """Return the ``NetworkSettings.Networks`` map for ``container`` via the CLI."""
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .NetworkSettings.Networks}}",
            container,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    out = result.stdout.strip()
    return json.loads(out) if out else {}


def resolve_docker_network(
    configured_network: str | None,
    *,
    hostname_path: Path = Path("/etc/hostname"),
    inspect_networks: Callable[[str], dict] = _docker_inspect_networks,
) -> str:
    """Resolve the Docker network for sibling containers.

    Precedence: an explicit ``configured_network`` wins; otherwise the web
    container's own non-``bridge`` network (auto-detected from ``/etc/hostname``
    → ``docker inspect``) so siblings share the compose network and can reach
    ``postgres`` by hostname; failing that, ``"bridge"``. ``hostname_path`` and
    ``inspect_networks`` are injectable for testing without a daemon.
    """
    if configured_network:
        return configured_network

    try:
        hostname = hostname_path.read_text(encoding="utf-8").strip()
        networks = inspect_networks(hostname)
        if isinstance(networks, dict):
            for name in networks:
                if isinstance(name, str) and name != "bridge":
                    return name
    except (subprocess.SubprocessError, OSError, ValueError) as error:
        logger.warning("Failed to auto-detect Docker network: %s", error)

    return "bridge"
