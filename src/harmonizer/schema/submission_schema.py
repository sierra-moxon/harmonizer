"""Thin schema-access wrappers over the nmdc-submission-schema.

Authored directly on ``linkml_runtime``'s :class:`SchemaView`; no schema-access
Python is vendored from other repos. The schema YAML itself is the only reuse and
is read from the pip-installed ``nmdc-submission-schema`` package via
:mod:`importlib.resources`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from importlib.resources import files

from linkml_runtime import SchemaView
from linkml_runtime.linkml_model.meta import SlotDefinition

_SCHEMA_PACKAGE = "nmdc_submission_schema"
_SCHEMA_RESOURCE = "schema/nmdc_submission_schema.yaml"


@cache
def get_schema_view() -> SchemaView:
    """Return a cached :class:`SchemaView` over the submission schema YAML.

    The YAML is located inside the installed ``nmdc-submission-schema`` package
    rather than vendored into this repo.
    """
    resource = files(_SCHEMA_PACKAGE).joinpath(_SCHEMA_RESOURCE)
    return SchemaView(str(resource))


def list_interfaces() -> list[str]:
    """Return the concrete "interface" class names, derived from schema metadata.

    Interfaces are the data-harmonizer template classes (the per-environment
    sheets). We derive them from metadata rather than a hardcoded list: classes
    whose name ends in ``Interface`` and which are neither ``abstract`` nor
    ``mixin``.
    """
    sv = get_schema_view()
    interfaces = [
        name
        for name, cls in sv.all_classes().items()
        if name.endswith("Interface") and not cls.abstract and not cls.mixin
    ]
    return sorted(interfaces)


def get_slots(interface: str) -> list[SlotDefinition]:
    """Return the induced slots for ``interface``.

    Raises :class:`ValueError` if the class is unknown.
    """
    sv = get_schema_view()
    if interface not in sv.all_classes():
        raise ValueError(f"unknown class: {interface!r}")
    return sv.class_induced_slots(interface)


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating a single value against a slot."""

    valid: bool
    reason: str = ""


def validate_value(
    slot: str, value: str, interface: str | None = None
) -> ValidationResult:
    """Validate ``value`` against ``slot``'s enum / pattern / range constraints.

    If ``interface`` is given, the slot is resolved in that class context
    (``induced_slot``); otherwise the global slot definition is used. Only the
    constraints the schema actually declares are checked; a slot with no enum,
    pattern, or recognized type range validates any value.
    """
    sv = get_schema_view()

    if interface is not None:
        if interface not in sv.all_classes():
            raise ValueError(f"unknown class: {interface!r}")
        slot_def = sv.induced_slot(slot, interface)
    else:
        slot_def = sv.get_slot(slot)

    if slot_def is None:
        raise ValueError(f"unknown slot: {slot!r}")

    range_name = slot_def.range

    # Enum range: value must be a permissible value (matched by text or meaning).
    if range_name in sv.all_enums():
        enum = sv.get_enum(range_name)
        for pv_name, pv in enum.permissible_values.items():
            if value == pv_name or (pv.meaning and value == pv.meaning):
                return ValidationResult(True)
        allowed = ", ".join(sorted(enum.permissible_values))
        return ValidationResult(
            False, f"{value!r} not in enum {range_name} (allowed: {allowed})"
        )

    # Pattern constraint.
    if slot_def.pattern and not re.match(slot_def.pattern, value):
        return ValidationResult(
            False, f"{value!r} does not match pattern for slot {slot!r}"
        )

    return ValidationResult(True)
