---
name: spreadsheet-to-nmdc
description: >-
  Orchestrator skill for mapping an uploaded spreadsheet to the
  nmdc-submission-schema: review inputs, resolve each placeholder per column
  with evidence, validate via schema tools, then write the curation report.
---

# Spreadsheet → NMDC

You are mapping an uploaded spreadsheet onto the `nmdc-submission-schema`. A
deterministic pre-pass has already run: it guessed the target interface and
per-column slots, and it left **placeholders** for everything it could not
resolve. Your task is to work that placeholder queue to completion — resolving
each with evidence or explicitly refusing — and then record the outcomes.

Follow `nmdc-curation-rules` at all times. Use `nmdc-env-triad` for the
environmental triad slots and `nmdc-taxon-resolution` for organism/taxon slots.

## Tools available (harmonizer MCP)

- `list_interfaces()` — the concrete interface (template) classes in the schema.
- `get_slots(interface)` — the slots for an interface: names, ranges, patterns,
  enums, descriptions, examples.
- `validate_value(slot, value, interface=None)` — check a value against a slot's
  constraints before you commit it.
- `record_mapping(column, slot, value, evidence=..., row="*")` — record a resolved
  value with its supporting evidence (persists to the ledger and the curation
  report). The first positional is the `column`; `row` is an optional keyword that
  defaults to the column-scope sentinel `"*"`.
- `leave_placeholder(column, reason, slot=None, row="*")` — record a deliberate
  refusal for a placeholder you cannot resolve. The order is `(column, reason)`;
  `slot` and `row` are optional keywords.
- `execute_code(code)` — run Python in the executor. The spreadsheet is bound to
  the `data` DataFrame, and `runoak` is on `PATH` for ontology lookups (ENVO,
  NCBITaxon, and others).

Column-scoped placeholders use the default `row="*"`; value-scoped resolutions
pass a concrete `row` id keyword.

## Step 1 — Review the inputs

Understand what you are mapping before you touch a single placeholder.

1. Read the job's sidecars in the workspace: `draft_mapping.json` (guessed
   interface + per-column proposed slots and status), `curation_inputs.json`
   (per-column samples, headers, study context), and `curation_report.json` (the
   ledger skeleton — the placeholders you must resolve).
2. Confirm the interface. Call `list_interfaces()` and, for the guessed
   interface, `get_slots(interface)` to learn the real slot names, ranges, and
   constraints you will be validating against.
3. Inspect the data with `execute_code`: look at column headers, dtypes, and a
   handful of representative values per placeholder column (`data.head()`,
   `data[col].unique()[:20]`). Note the study context — it is often what
   disambiguates a value.

## Step 2 — Resolve placeholders, one column at a time

Walk the placeholder queue. For each placeholder:

1. Identify the target slot from `draft_mapping.json` / `get_slots`, and read the
   slot's description, range, and any enum or pattern.
2. Gather evidence:
   - **Schema-constrained slots** (enums / patterns): choose the permissible
     value that matches the source; let `validate_value` confirm it.
   - **Environmental triad slots**: apply `nmdc-env-triad` — search ENVO with
     `runoak`, build a flat `label [ENVO:CURIE]` string.
   - **Taxon slots**: apply `nmdc-taxon-resolution` — search NCBITaxon with
     `runoak`, build a flat `label [NCBITaxon:CURIE]` string.
   - **Free-text / plain slots**: map the source value faithfully; do not enrich.
3. If a column's *values* need per-value resolution (e.g. each distinct cell must
   become a CURIE), resolve each distinct value with its own `row` id, not just
   the column mapping.
4. Decide the outcome (see Step 3) and record it.

Resolve on evidence, not on the pre-pass's guess: the guessed slot is a starting
hypothesis, and you may correct it when the evidence points elsewhere.

## Step 3 — Validate before recording

Never record an unvalidated value.

1. Call `validate_value(slot, value, interface=<guessed interface>)` for every
   candidate value.
2. If it **passes**, call `record_mapping(column, slot, value, evidence=...)` with
   a concrete evidence citation (schema quote, or the `runoak` CURIE + label, or
   the source cell). Pass `row="<id>"` for a value-scoped resolution.
3. If it **fails**, do not record it. Either find a valid alternative that still
   faithfully represents the source, or, if none exists, call
   `leave_placeholder(column, reason, slot=...)` describing why.
4. If you cannot resolve a placeholder with confidence, call
   `leave_placeholder` — refusing is a correct outcome, not a failure.

## Step 4 — Write the curation report

When every placeholder has a tracked outcome, make sure the ledger reflects
reality. Each placeholder must end as exactly one of:

- **`resolved`** — a validated value was recorded via `record_mapping`, with
  evidence.
- **`left_placeholder`** — you deliberately refused via `leave_placeholder`, with
  a reason.
- **`validator_rejected`** — a candidate failed `validate_value` and no valid
  alternative was found (surfaced through your refusal).

Do a final pass over `curation_report.json`: confirm there are no untouched
placeholders, that every recorded value carries evidence, that every refusal
carries a reason, and that no placeholder text leaked into any conformant
value. Summarize what was resolved, what was refused, and why — that report is
the audit trail a human curator will trust.
