"""MCP stdio server exposing the harmonizer mapping toolset.

Patterned on OpenScientist's ``openscientist_tools`` (pattern only; the code is
authored here). A single :class:`~mcp.server.fastmcp.FastMCP` instance in
:mod:`harmonizer_tools.server` gathers three tool families:

* schema tools — thin pass-throughs to the Phase 0 schema-access layer;
* ledger tools — persist agent decisions into ``MappingState`` and keep
  ``curation_report.json`` in sync;
* ``execute_code`` — run python against the job's sheet (``data``) with
  ``runoak`` available on PATH for ontology lookups.

Per-job context (``job_id``, ``job_dir``, data files) is bound from
``HARMONIZER_JOB_*`` environment variables via
:class:`harmonizer_tools.state.ToolState`.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
