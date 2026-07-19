"""Schema tools: JSON-serializable pass-throughs to the Phase 0 access layer.

These carry no per-job state; they simply adapt
:mod:`harmonizer.schema.submission_schema` into MCP-friendly return shapes
(``SlotDefinition`` objects become plain dicts, ``ValidationResult`` becomes a
dict).
"""

from __future__ import annotations

from harmonizer.schema import submission_schema
from harmonizer_tools.server import mcp


def _slot_summary(slot) -> dict:
    """Reduce a ``SlotDefinition`` to the fields the agent needs to map a column."""
    return {
        "name": slot.name,
        "title": slot.title,
        "description": slot.description,
        "range": slot.range,
        "required": bool(slot.required),
        "multivalued": bool(slot.multivalued),
        "pattern": slot.pattern,
        "aliases": list(slot.aliases or []),
    }


@mcp.tool()
def list_interfaces() -> list[str]:
    """List the concrete submission-schema interfaces (per-environment sheets)."""
    return submission_schema.list_interfaces()


@mcp.tool()
def get_slots(interface: str) -> list[dict]:
    """Return the induced slots for ``interface`` as JSON-serializable summaries.

    Raises ``ValueError`` if the interface is unknown.
    """
    return [_slot_summary(slot) for slot in submission_schema.get_slots(interface)]


@mcp.tool()
def validate_value(slot: str, value: str, interface: str | None = None) -> dict:
    """Validate ``value`` against ``slot`` (optionally within ``interface``).

    Returns ``{"valid": bool, "reason": str}``. Raises ``ValueError`` if the slot
    or interface is unknown.
    """
    result = submission_schema.validate_value(slot, value, interface)
    return {"valid": result.valid, "reason": result.reason}
