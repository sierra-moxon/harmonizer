---
name: nmdc-env-triad
description: >-
  Resolve the environmental triad (env_broad_scale, env_local_scale,
  env_medium) to ENVO terms using the submission-schema flat "label [CURIE]"
  string convention. Includes judgment and refuse rules.
---

# NMDC Environmental Triad

The environmental triad describes *where* a sample came from at three nested
scales. It maps to three slots in the submission schema:

- **`env_broad_scale`** — the broad biome / major environmental system the
  sample sits within (an ENVO biome term, e.g. a terrestrial or aquatic biome).
- **`env_local_scale`** — the local environmental feature immediately around the
  sample (a landform, structure, or feature — the setting you would point to).
- **`env_medium`** — the material the sample was actually taken *from* (the
  substance in contact with the sampled material — soil, water, sediment, etc.).

Read `nmdc-curation-rules` first. Everything below is subordinate to
evidence-first, refuse-when-unsure, and placeholder discipline.

## Value shape: flat `"label [CURIE]"` strings

The submission schema stores each triad value as a **single flat string** of the
form:

```
label [CURIE]
```

Examples:

- `soil [ENVO:00001998]`
- `agricultural field [ENVO:00000114]`
- `terrestrial biome [ENVO:00000446]`

The `label` is the ontology's own label for the term; the `CURIE` is its
compact identifier in `PREFIX:localid` form (here always an `ENVO:` term). There
is exactly one space before the `[`, and the CURIE sits inside square brackets
with no extra whitespace.

> **Do not** emit a nested object such as
> `{"has_raw_value": ..., "term": {"id": ...}}`. That
> `ControlledIdentifiedTermValue` shape belongs to the *full* nmdc-schema, not to
> the submission schema. The submission schema wants the flat string above.
> Emitting the nested object is a validation error.

Always confirm the exact label the ontology returns and paste it verbatim into
the string — do not paraphrase the label.

## Resolution procedure

1. **Read the source.** Inspect the column header, the study context, and the
   actual cell values with `execute_code` (`data` is the sheet). A single column
   may already name one triad member; sometimes broad/local/medium are spread
   across several columns or must be inferred from a free-text description.
2. **Search ENVO with `runoak`.** For each triad member, search for the concept
   the source describes:

   ```
   runoak -i sqlite:obo:envo search "soil"
   runoak -i sqlite:obo:envo info ENVO:00001998
   ```

   Pick the term whose definition genuinely matches the source concept at the
   correct scale — a *biome* for broad, a *feature* for local, a *material* for
   medium. Do not put a material term in the broad-scale slot.
3. **Assemble the flat string.** Combine the returned label and CURIE:
   `label [ENVO:xxxxxxx]`.
4. **Validate.** Call `validate_value` for the triad slot (with the interface
   context) before recording. Only record values that validate.
5. **Record with evidence.** Call `record_mapping` citing the search term and the
   ENVO CURIE/label you selected.

## Judgment and refuse rules

- **Respect the scale.** If a value clearly belongs to a *different* triad member
  than the slot being filled (e.g. the header says "medium" but the value is a
  biome), resolve it to the slot it truly matches or refuse — do not force a
  scale mismatch.
- **Refuse on ambiguity.** If ENVO search yields no confident match, or several
  candidates you cannot separate from the available context, call
  `leave_placeholder` with a specific reason (e.g. "'wetland edge' spans biome
  and feature; source does not disambiguate").
- **Do not invent terms.** If you cannot find an ENVO term for the concept, that
  is a placeholder, not a licence to coin a CURIE.
- **Free-text and conflated cells.** A cell naming two environments, or describing
  a gradient, generally cannot be captured by one term — refuse and flag it for a
  human curator rather than picking one arbitrarily.
