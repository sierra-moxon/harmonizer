"""``execute_code``: run python against the job's sheet.

The in-process executor exposes the job's spreadsheet as ``data`` (a pandas
DataFrame) plus ``pd`` in the namespace, captures ``stdout``, and returns any
traceback as ``error``. ``runoak`` is available on PATH (via ``oaklib``), so code
may shell out for ontology lookups. Container isolation (Phase 9) will replace
this in-process path behind a feature flag; the tool contract stays the same.
"""

from __future__ import annotations

import contextlib
import io
import traceback

import pandas as pd

from harmonizer_tools.server import mcp
from harmonizer_tools.state import get_state


@mcp.tool()
def execute_code(code: str) -> dict:
    """Execute ``code`` with the job's sheet bound to ``data``.

    Returns ``{"stdout": str, "error": str | None}``. ``error`` is ``None`` on
    success or a formatted traceback string on failure; ``stdout`` always holds
    whatever was printed before any failure.
    """
    state = get_state()
    namespace: dict = {
        "data": state.load_dataframe(),
        "pd": pd,
    }
    stdout = io.StringIO()
    error: str | None = None
    try:
        with contextlib.redirect_stdout(stdout):
            exec(code, namespace)  # noqa: S102 - agent code-execution tool by design
    except Exception:
        error = traceback.format_exc()
    return {"stdout": stdout.getvalue(), "error": error}
