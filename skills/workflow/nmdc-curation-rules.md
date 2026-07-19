---
name: nmdc-curation-rules
description: >-
  Schema-agnostic governance for curating spreadsheet-to-NMDC mappings:
  evidence-first resolution, refuse-when-unsure, and placeholder discipline.
  Read this first; it governs every other mapping skill.
---

# NMDC Curation Rules

These rules govern *how* you resolve a placeholder, independent of any
particular slot or ontology. They apply to every column and every value you
touch. When a more specific skill (env-triad, taxon-resolution) conflicts with
these rules, these rules win.

## 1. Evidence-first

Never assert a mapping you cannot justify. Every resolution you record must be
backed by concrete evidence drawn from one of:

- **The schema.** A slot definition, its `range`, `pattern`, enum permissible
  values, description, or examples (via `get_slots` / `validate_value`).
- **An ontology lookup.** A CURIE and label returned by `runoak` (e.g.
  `runoak -i sqlite:obo:envo search ...` or `runoak -i sqlite:obo:envo info
  ENVO:00001998`). Quote the label the ontology returns; do not invent it.
- **The spreadsheet itself.** The column header, the study context, and the
  actual cell values (inspect them with `execute_code`; the sheet is bound to
  `data`).

Record the evidence when you call `record_mapping` — cite the source and a short
quote or paraphrase. A mapping with no traceable evidence is not allowed.

## 2. Refuse when unsure

Refusing is a first-class, correct outcome — not a failure. If, after gathering
evidence, you still cannot resolve a value to the required confidence, call
`leave_placeholder` with a specific `reason`. Prefer an honest placeholder over
a plausible-but-unverified guess.

Refuse (leave a placeholder) when:

- The ontology search returns **no** confident match, or several equally-ranked
  candidates you cannot disambiguate from the available context.
- The cell value is ambiguous, free-text, or conflates multiple concepts
  ("forest/grassland edge") that no single term captures.
- The proposed value **fails** `validate_value` and you cannot find a valid
  alternative that still faithfully represents the source.
- The correct answer would require information not present in the spreadsheet,
  the study context, or the schema.

A wrong value is worse than an acknowledged gap: downstream curators can fill a
flagged placeholder, but a confident-looking error can slip through unaudited.

## 3. Placeholder discipline

Placeholders are deliberate markers the deterministic pre-pass wrote into slots
it could not resolve. Treat them as a work queue, not as data.

- **Resolve or refuse — never silently drop.** Every placeholder must end with a
  tracked outcome: `resolved` (via `record_mapping`) or `left_placeholder` (via
  `leave_placeholder`). If a validator rejects your value, that is a
  `validator_rejected` outcome — try again or refuse; do not paper over it.
- **One placeholder, one decision.** Do not batch unrelated columns into a single
  resolution. Resolve each column (and, where required, each distinct value)
  on its own evidence.
- **Do not write placeholder text into the artifact as if it were real.** The
  curation report is where unresolved items live; the conformant artifact must
  only ever contain validated values.
- **Column-scoped vs value-scoped.** A column-scoped placeholder (`row="*"`)
  concerns the slot mapping for the whole column. Value-scoped resolutions carry
  a concrete `row` id. Keep the scope you were given unless the evidence forces a
  split.

## 4. Faithfulness over completeness

Your job is to represent what the data *says*, not to enrich it. Do not
normalize, correct, or "improve" a source value beyond what the evidence
supports. If the sheet says `dirt`, resolve it to the soil term the ontology
actually returns for that concept — do not upgrade `dirt` into a more specific
soil type the author never claimed.

## 5. Validate before you commit

Before recording any value against a slot, pass it through `validate_value`
(with the interface context when known). Only record values the validator
accepts. If validation fails, the value is not a resolution — it is either a
retry or a refusal.
