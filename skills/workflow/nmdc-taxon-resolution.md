---
name: nmdc-taxon-resolution
description: >-
  Resolve organism / taxon values to NCBITaxon terms using the submission-schema
  flat "label [CURIE]" string convention. Includes judgment and refuse rules.
---

# NMDC Taxon Resolution

Taxon slots identify the organism (or the host organism) associated with a
sample — for example the sampled organism, a host species, or a specific
declared taxon. These resolve against **NCBITaxon**.

Read `nmdc-curation-rules` first. Everything below is subordinate to
evidence-first, refuse-when-unsure, and placeholder discipline.

## Value shape: flat `"label [CURIE]"` strings

Like the environmental triad, taxon values in the submission schema are a
**single flat string**:

```
label [CURIE]
```

Examples:

- `Homo sapiens [NCBITaxon:9606]`
- `Zea mays [NCBITaxon:4577]`
- `Escherichia coli [NCBITaxon:562]`

The `label` is NCBITaxon's scientific name for the taxon; the `CURIE` is its
`NCBITaxon:<taxid>` identifier. One space precedes the `[`; the CURIE sits inside
the brackets with no extra whitespace.

> **Do not** emit a nested `ControlledIdentifiedTermValue` object. The submission
> schema wants the flat string above, not the full-schema nested term shape.
> Emitting the nested object is a validation error.

Use the scientific name NCBITaxon returns as the label — not a common name and
not the source spelling if it differs. If the source gives a common name
("maize", "corn"), resolve it to the scientific-name label (`Zea mays`) via the
ontology; keep the common name only as evidence, not as the recorded label.

## Resolution procedure

1. **Read the source.** Inspect the column header, study context, and cell values
   with `execute_code` (`data` is the sheet). Values may be scientific names,
   common names, strain designations, or abbreviations.
2. **Search NCBITaxon with `runoak`:**

   ```
   runoak -i sqlite:obo:ncbitaxon search "Zea mays"
   runoak -i sqlite:obo:ncbitaxon info NCBITaxon:4577
   ```

   Match at the **rank the source actually specifies**. If the source names a
   species, resolve to that species; do not silently promote to genus or demote
   to strain.
3. **Assemble the flat string:** `label [NCBITaxon:xxxx]`, using the returned
   scientific name.
4. **Validate.** Call `validate_value` for the taxon slot (with interface
   context) before recording.
5. **Record with evidence.** Call `record_mapping` citing the search term and the
   NCBITaxon CURIE/label you selected.

## Judgment and refuse rules

- **Rank fidelity.** Resolve to the rank the source claims. If the source is only
  specific to genus ("*Bacillus* sp."), record the genus term — do not invent a
  species.
- **Ambiguous common names.** Common names that map to several taxa ("bass",
  "cedar") must be disambiguated from the study context; if they cannot be,
  `leave_placeholder` with a reason.
- **Strains and unrecognized names.** If a strain or name has no NCBITaxon entry,
  resolve to the nearest *validated* ancestor the source supports, or refuse —
  never coin a taxid.
- **No confident match.** If NCBITaxon search returns nothing convincing, call
  `leave_placeholder` rather than guessing. An honest gap beats a wrong organism.
- **Host vs. sampled organism.** Make sure you are filling the correct slot: a
  host-organism value must not be recorded into a sampled-organism slot (or vice
  versa). If the source does not make the distinction clear, refuse.
