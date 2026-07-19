"""Harmonizer web UI (NiceGUI + FastAPI).

Importing this package registers the pages (via :mod:`harmonizer.web.app`) and
exposes :func:`run` for ``python -m harmonizer.web``.
"""

from __future__ import annotations

from harmonizer.web.app import get_manager, run, set_manager

__all__ = ["get_manager", "run", "set_manager"]
