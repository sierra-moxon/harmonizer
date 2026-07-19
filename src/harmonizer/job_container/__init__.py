"""Per-job Docker isolation (Phase 9).

Exposes :class:`~harmonizer.job_container.runner.JobContainerRunner`, which
launches one container per mapping run so each job gets a reproducible, isolated
environment shipping ``runoak`` + prefetched ontologies. In-process execution
remains the default; container isolation is gated behind
``settings.use_container_isolation``.
"""

from __future__ import annotations

from harmonizer.job_container.runner import ContainerSpec, JobContainerRunner

__all__ = ["ContainerSpec", "JobContainerRunner"]
