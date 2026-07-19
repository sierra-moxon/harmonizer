"""Tests for the schema tools (Phase 3): pass-throughs to the Phase 0 layer."""

from __future__ import annotations

import pytest

from harmonizer_tools.schema_tools import (
    get_slots,
    list_interfaces,
    validate_value,
)


def test_list_interfaces_includes_soil():
    interfaces = list_interfaces()
    assert "SoilInterface" in interfaces
    assert interfaces == sorted(interfaces)


def test_get_slots_returns_serializable_summaries():
    slots = get_slots("SoilInterface")
    assert slots, "expected at least one slot"
    by_name = {s["name"]: s for s in slots}
    assert "env_broad_scale" in by_name
    summary = by_name["env_broad_scale"]
    # Summary is a plain, JSON-serializable dict with the expected keys.
    assert set(summary) == {
        "name",
        "title",
        "description",
        "range",
        "required",
        "multivalued",
        "pattern",
        "aliases",
    }
    assert isinstance(summary["required"], bool)
    assert isinstance(summary["aliases"], list)


def test_get_slots_unknown_interface_raises():
    with pytest.raises(ValueError):
        get_slots("NotARealInterface")


def test_validate_value_returns_valid_dict():
    # A free-text-ish slot accepts arbitrary values.
    result = validate_value("samp_name", "sample-001", "SoilInterface")
    assert result == {"valid": True, "reason": ""}


def test_validate_value_reports_reason_on_failure():
    slots = {s["name"]: s for s in get_slots("SoilInterface")}
    enum_slot = next(
        (
            name
            for name, s in slots.items()
            if s["range"] and s["range"].endswith("Enum")
        ),
        None,
    )
    if enum_slot is None:
        pytest.skip("no enum-ranged slot on SoilInterface to exercise rejection")
    result = validate_value(enum_slot, "definitely-not-a-permissible-value")
    assert result["valid"] is False
    assert result["reason"]
