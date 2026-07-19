"""Prompt construction for the mapping loop.

Three prompts drive a run:

* :func:`build_system_prompt` — standing mapping capabilities plus a pointer to
  the workflow skills materialized in ``.claude/skills/``. Passed once as the
  agent's system prompt.
* :func:`build_initial_prompt` — the kickoff turn: orients the agent to the
  draft mapping, the curation inputs, and the study context in the workspace.
* :func:`build_iteration_prompt` — a per-turn prompt summarizing the remaining
  placeholders from :class:`~harmonizer.state.mapping_state.MappingState` and
  instructing the agent to resolve or refuse each, using the real MCP tool
  signatures.

The prompts reference the **authoritative** MCP tool signatures (the ledger
tools take ``column`` first, not ``row``); see
:mod:`harmonizer_tools.ledger_tools` and :mod:`harmonizer_tools.schema_tools`.

Mirrors OpenScientist's ``orchestrator/iteration.py::build_*_prompt`` (pattern
only; authored here).
"""

from __future__ import annotations

from harmonizer.agent.skills import CLAUDE_SKILLS_SUBPATH, ENABLED_SKILLS
from harmonizer.state.mapping_state import MappingState, PlaceholderEntry

#: Sidecar files the deterministic pre-pass writes at the job-dir root.
_DRAFT_MAPPING = "draft_mapping.json"
_CURATION_INPUTS = "curation_inputs.json"
_CURATION_REPORT = "curation_report.json"

#: Where the workflow skills are materialized under a job dir.
_SKILLS_DIR = "/".join(CLAUDE_SKILLS_SUBPATH)

#: How many sample values to surface per placeholder in the iteration prompt.
_MAX_SAMPLES = 5


def _tool_reference() -> str:
    """Return the authoritative MCP tool signatures block (shared by prompts)."""
    return (
        "Tools (harmonizer MCP; call them by these exact names and signatures):\n"
        "- list_interfaces() -> list[str]\n"
        "- get_slots(interface) -> list[dict]\n"
        "- validate_value(slot, value, interface=None) -> dict\n"
        "- record_mapping(column, slot, value, evidence=..., row=\"*\") -> dict\n"
        "    First positional is the column; row defaults to the column-scope\n"
        "    sentinel \"*\"; pass row=\"<id>\" for a value-scoped resolution.\n"
        "    Evidence items are {\"source\": ..., \"quote_or_paraphrase\": ...}.\n"
        "- leave_placeholder(column, reason, slot=None, row=\"*\") -> dict\n"
        "    Order is (column, reason); slot and row are optional keywords.\n"
        "- execute_code(code) -> dict  (the sheet is bound to the `data`"
        " DataFrame; runoak is on PATH)"
    )


def build_system_prompt() -> str:
    """Build the standing system prompt: capabilities + skills pointer.

    Names the enabled workflow skills and points at where they are materialized
    (``.claude/skills/``) so the agent knows the methodology is available.
    """
    skills = "\n".join(f"  - {name}" for name in ENABLED_SKILLS)
    return (
        "You are the mapping agent for Harmonizer. You map columns and values of "
        "an uploaded spreadsheet onto the nmdc-submission-schema, resolving each "
        "placeholder with evidence or explicitly refusing when unsure.\n\n"
        "You never decide how many iterations to run — the orchestrator owns the "
        "loop. Your job each turn is to resolve or refuse the placeholders you are "
        "given, recording every outcome through the ledger tools.\n\n"
        "Methodology skills are materialized in this workspace under "
        f"`{_SKILLS_DIR}/` (read them before acting):\n"
        f"{skills}\n\n"
        "Follow `nmdc-curation-rules` at all times: evidence-first, refuse when "
        "unsure, and never let a placeholder marker leak into a conformant value.\n\n"
        f"{_tool_reference()}"
    )


def build_initial_prompt(
    state: MappingState,
    study_context: str = "",
) -> str:
    """Build the kickoff prompt referencing the draft mapping and inputs.

    Orients the agent to the sidecars in the workspace and the study context,
    and hands it the placeholder queue it must work to completion.
    """
    remaining = state.remaining_placeholders()
    lines = [
        "Begin mapping this spreadsheet to the nmdc-submission-schema.",
        "",
        f"Guessed interface: {state.interface_guess or '(none guessed)'}",
        f"Source file: {state.source_filename or '(unknown)'}",
        "",
        "The deterministic pre-pass wrote these sidecars at the workspace root:",
        f"- {_DRAFT_MAPPING}: guessed interface + per-column proposed slots/status",
        f"- {_CURATION_INPUTS}: per-column samples, headers, and study context",
        f"- {_CURATION_REPORT}: the ledger skeleton (placeholders to resolve)",
        "",
        "Read all three before acting, then confirm the interface with "
        "list_interfaces() / get_slots(interface).",
    ]
    if study_context.strip():
        lines += ["", "Study context:", study_context.strip()]
    lines += [
        "",
        _placeholder_summary(remaining),
        "",
        _tool_reference(),
    ]
    return "\n".join(lines)


def build_iteration_prompt(
    state: MappingState,
    iteration: int | None = None,
) -> str:
    """Build a per-turn prompt summarizing the remaining placeholders.

    Lists each outstanding placeholder (column, proposed slot, sample values)
    and instructs the agent to resolve it with evidence or refuse via the ledger
    tools. If nothing remains, says so.
    """
    remaining = state.remaining_placeholders()
    header = "Continue mapping."
    if iteration is not None:
        header = f"Iteration {iteration}: continue mapping."
    lines = [
        header,
        "",
        _placeholder_summary(remaining),
    ]
    if remaining:
        lines += [
            "",
            "For each outstanding placeholder:",
            "1. Inspect the column's values with execute_code and read the slot's "
            "constraints via get_slots.",
            "2. Gather evidence (schema quote, runoak CURIE+label, or the source "
            "cell) and confirm the candidate with validate_value before recording.",
            "3. Record the outcome: record_mapping(column, slot, value, "
            "evidence=...) on success, or leave_placeholder(column, reason) when "
            "you cannot resolve it with confidence. Refusing is a valid outcome.",
            "",
            "Do not re-touch placeholders already resolved or deliberately left.",
            "",
            _tool_reference(),
        ]
    return "\n".join(lines)


def _placeholder_summary(remaining: list[PlaceholderEntry]) -> str:
    """Render the outstanding-placeholder list (or a done message)."""
    if not remaining:
        return "No placeholders remain; every column has a tracked outcome."
    lines = [f"Outstanding placeholders ({len(remaining)}):"]
    for entry in remaining:
        slot = entry.proposed_slot or "(no slot guessed)"
        scope = "" if entry.row_id == "*" else f" [row {entry.row_id}]"
        lines.append(f"- column {entry.column!r}{scope} -> proposed slot {slot!r}")
        samples = _entry_samples(entry)
        if samples:
            lines.append(f"    samples: {samples}")
    return "\n".join(lines)


def _entry_samples(entry: PlaceholderEntry) -> str:
    """Best-effort sample string for a placeholder, if evidence carries any."""
    if entry.value:
        return repr(entry.value)
    samples: list[str] = []
    for item in entry.evidence or []:
        text = item.get("quote_or_paraphrase") if isinstance(item, dict) else None
        if text:
            samples.append(str(text))
        if len(samples) >= _MAX_SAMPLES:
            break
    return ", ".join(samples)
