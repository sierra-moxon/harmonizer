"""Orchestrator: the iteration loop that resolves placeholders.

The orchestrator owns the mapping loop — iteration count, prompt construction,
retries, persistence, and the "freshness guard." The agent owns a single turn's
judgment (which slot, which value, cite evidence or refuse) and never controls
iteration count.

:mod:`harmonizer.orchestrator.prompts` builds the system / initial / iteration
prompts; :mod:`harmonizer.orchestrator.loop` drives the loop
(:func:`run_mapping_async`).

Mirrors OpenScientist's ``orchestrator/discovery.py`` +
``orchestrator/iteration.py`` (pattern only; authored here).
"""

from __future__ import annotations

from harmonizer.orchestrator.loop import run_mapping_async
from harmonizer.orchestrator.prompts import (
    build_initial_prompt,
    build_iteration_prompt,
    build_system_prompt,
)
from harmonizer.orchestrator.report import run_report_phase

__all__ = [
    "build_initial_prompt",
    "build_iteration_prompt",
    "build_system_prompt",
    "run_mapping_async",
    "run_report_phase",
]
