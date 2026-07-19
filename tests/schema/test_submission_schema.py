"""Tests for the submission-schema access layer (Phase 0)."""

from linkml_runtime import SchemaView

from harmonizer.schema.submission_schema import (
    ValidationResult,
    get_schema_view,
    get_slots,
    list_interfaces,
    validate_value,
)


def test_get_schema_view_loads():
    sv = get_schema_view()
    assert isinstance(sv, SchemaView)
    assert len(sv.all_classes()) > 0


def test_get_schema_view_is_cached():
    assert get_schema_view() is get_schema_view()


def test_list_interfaces_derived_from_metadata():
    interfaces = list_interfaces()
    assert interfaces == sorted(interfaces)
    assert all(name.endswith("Interface") for name in interfaces)
    assert "SoilInterface" in interfaces


def test_list_interfaces_excludes_abstract_and_mixin():
    sv = get_schema_view()
    interfaces = set(list_interfaces())
    for name in interfaces:
        cls = sv.get_class(name)
        assert not cls.abstract
        assert not cls.mixin


def test_get_slots_returns_induced_slots():
    slots = get_slots("SoilInterface")
    names = {s.name for s in slots}
    assert "biotic_relationship" in names
    assert len(slots) > 0


def test_get_slots_unknown_class_raises():
    import pytest

    with pytest.raises(ValueError):
        get_slots("NotARealInterface")


def test_validate_value_accepts_valid_enum():
    result = validate_value("biotic_relationship", "free living")
    assert isinstance(result, ValidationResult)
    assert result.valid


def test_validate_value_rejects_invalid_enum():
    result = validate_value("biotic_relationship", "not-a-relationship")
    assert not result.valid
    assert "enum" in result.reason


def test_validate_value_unknown_slot_raises():
    import pytest

    with pytest.raises(ValueError):
        validate_value("definitely_not_a_slot", "x")


def test_validate_value_pattern_rejects_bad_shape():
    # agrochem_addition carries a regex pattern in the schema.
    result = validate_value("agrochem_addition", "clearly not matching")
    assert not result.valid
