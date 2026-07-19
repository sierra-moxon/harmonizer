"""FastMCP instance and stdio bootstrap for ``harmonizer_tools``.

The shared :data:`mcp` instance is created here so the tool modules can register
against it. :func:`register_tools` imports those modules for their registration
side-effects; :func:`main` wires the per-job state and starts the stdio server.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("harmonizer-tools")


def register_tools() -> FastMCP:
    """Import the tool modules so their ``@mcp.tool()`` decorators run.

    Importing is the registration mechanism (idempotent); returns the populated
    server for convenience.
    """
    from harmonizer_tools import execute_code, ledger_tools, schema_tools  # noqa: F401

    return mcp


def main() -> None:
    """Register the tools and serve over stdio (``python -m harmonizer_tools``)."""
    register_tools()
    mcp.run()
