# Implementation Plan: Spreadsheet → NMDC Schema Mapping Agent

## Purpose

Repurpose the OpenScientist infrastructure (agent executor, orchestrator loop,
MCP tool subprocess, per-job Docker isolation, provider abstraction, web/job UI)
to build an **iterative agent that maps arbitrary spreadsheets to the
`nmdc-submission-schema`**. We keep the *infrastructure* and replace the
*domain payload* (skills, tools, state, prompts).

This is **not** a fork of the discovery agent's science content. It is a
gut-and-reline: the loop stays, the brains change.

---

## Terminology

**Placeholder** — a deliberately-recognizable marker written into a slot the
deterministic pre-pass could not resolve, signalling "the agent must fill this."
(Elsewhere this is called a *sentinel*, e.g. the ingest-agent's `ENVO:00000000`.)
Every placeholder has a tracked outcome in the curation report:
`resolved`, `left_placeholder`, or `validator_rejected`.

---

## Reuse principles (READ FIRST)

1. **Copy-in applies to markdown skills only.** We may copy the ingest-agent's
   methodology `SKILL.md` files (they are prose guidance, not code).
2. **We write our own Python.** Deterministic logic is authored here against
   pip-installed libraries (e.g. `linkml-runtime`'s `SchemaView`), not copied from
   other repos.
3. **Copying any Python requires an explicit, documented justification.** The bar
   is: a genuinely complex, well-tested algorithm that is costly/risky to
   reimplement. Thin schema traversal does not clear this bar — we write it.
4. **Borrow knowledge, not code.** Where another repo encodes a useful decision
   (e.g. which classes are "interfaces", which slots to hide), we re-derive it from
   schema metadata rather than copy a hardcoded list.

---

## Locked decisions

| Decision | Choice |
| --- | --- |
| Runtime shape | **Iterative agent** (not one-shot pipeline) |
| Schema target | **`nmdc-submission-schema`** (portal-facing, human-authored) |
| Output artifacts | **Both**: schema-conformant sheet/JSON **and** a curation report |
| Ontology resolution | **`runoak`/OAK preinstalled in the `execute_code` container** |
| Schema access code | **We author thin wrappers on `linkml-runtime` `SchemaView`** (no Python vendoring) |
| Skill source | **Copy-in ingest-agent methodology skills**, drop suggestor function-doc skills |

---

## Source-repo provenance (what comes from where)

**Copy-in from `nmdc-ingest-agent` (markdown skills — genuine methodology):**
- `nmdc-curation-rules.md` → copy ~verbatim (schema-agnostic governance).
- `nmdc-env-triad.md`, `nmdc-taxon-resolution.md` → copy, then edit value-shape
  sections to submission-schema's flat `"label [CURIE]"` convention.
- `ncbi-to-nmdc.md` → do **not** copy; use its 8-step shape as the template for a new
  `spreadsheet-to-nmdc` orchestrator skill we author.
- Drop: `nmdc-target-gene.md` (amplicon-specific), `mfd-project-vocabulary.md`
  (project-specific). `nmdc-schema-reference.md` → borrow its methodology, don't copy.

**From `nmdc-metadata-suggestor-ai-tool`:**
- `nmdc-submission-schema` → **pip** dependency (the schema YAML). This is the only
  reuse; the schema is canonical and versioned.
- **No Python is copied.** We do NOT vendor `SchemaContextBuilder`,
  `models/schema.py`, or `constants.py`. We read the suggestor's interface/exclusion
  choices once as *reference*, then derive our own from schema metadata.
- Drop entirely: `submission_parser`, `build_submission_context`,
  `recommendation_pipeline`, `env_triad_recommendation`, `llm_client`, and all
  suggestor SKILL.md files.

**Reuse from OpenScientist (infrastructure, mostly unchanged):**
- Orchestrator loop, agent executor, provider abstraction, MCP subprocess pattern,
  Docker per-job isolation, web/job UI, auth.

---

## Why we do NOT vendor the suggestor's schema Python

`SchemaContextBuilder` is a thin wrapper over `SchemaView`, and `SchemaView` already
ships in `linkml-runtime` (which we pip anyway). Our MCP tools need only direct
`SchemaView` calls:

- `list_interfaces()` → `sv.all_classes()` filtered by suffix / derived from
  `abstract`/`mixin` flags.
- `get_slots(interface)` → `sv.class_induced_slots(interface)`.
- `validate_value(slot, value)` → enum/pattern/range from `sv.induced_slot(...)`.

The suggestor's added value (hardcoded exclusion lists, markdown-for-prompt
formatting, its own Pydantic models) is exactly what we want to differ on: a
different schema, JSON tool output instead of prompt-markdown, and no coupling to
their `constants.py`/`models`. Reimplementing is ~40 lines we own; copying drags two
coupled internal modules for no benefit. **Verdict: write our own.**

---

## Architecture: the runtime pattern

```
Deterministic pre-pass (new; runs in job setup)
  ├─ load spreadsheet, infer columns (pandas)
  ├─ emit DRAFT mapping with PLACEHOLDERS for unresolved columns/values
  ├─ write curation-inputs sidecar (evidence the agent needs)
  └─ write curation-report skeleton (the ledger)
        ↓
Iterative agent loop (OpenScientist orchestrator; control flow unchanged)
  each iteration:
    - prompt built from MappingState (which placeholders remain)
    - agent inspects sheet (execute_code + pandas), looks up schema slots,
      resolves values via runoak, applies evidence-first rules, REFUSES when unsure
    - agent records resolutions/refusals via ledger MCP tools → MappingState
        ↓
Report phase (OpenScientist; repurposed)
  └─ emit BOTH: schema-conformant sheet/JSON  +  curation report
```

**The deterministic/agent line:** the orchestrator owns the loop, prompt
construction, retries, and persistence. The agent owns per-placeholder judgment
(which slot, which value, cite evidence or refuse). It never controls iteration
count.

---

## Phased implementation

### Phase 0 — Dependencies & container

**Goal:** schema + ontology tooling available to code and agent.

1. Add `nmdc-submission-schema>=11.0.0` and `linkml-runtime` to `pyproject.toml`.
2. **Author** a thin schema-access module `src/openscientist/schema/submission_schema.py`
   directly on `linkml_runtime`'s `SchemaView` (no copied code):
   - `get_schema_view()` — cached `SchemaView` over the submission schema YAML,
     loaded via `importlib.resources`.
   - `list_interfaces()` — derive from schema metadata (suffix + `abstract`/`mixin`
     flags), not a hardcoded list.
   - `get_slots(interface)`, `validate_value(slot, value)` — enum/pattern/range checks.
3. Update the agent Docker image (built for `job_container`): preinstall
   `oaklib`/`runoak` and prefetch the ENVO + NCBITaxon sqlite databases so
   `runoak -i sqlite:obo:envo ...` works offline inside `execute_code`.

**Done when:** `get_schema_view()` loads in a unit test; `runoak info ENVO:00001998`
runs inside a built agent container.

---

### Phase 1 — State model: `MappingState` replaces `KnowledgeState`

**Goal:** a DB-backed ledger the loop accretes into.

1. Add a `MappingState` model (mirror the existing `KnowledgeState` DB access
   pattern — `load_from_database_sync`, `save_to_database_sync`).
2. Fields per placeholder row: `row_id`/`column`, `proposed_slot`, `value`,
   `confidence`, `evidence` (list of `{source, quote_or_paraphrase}`),
   `outcome` (`resolved` | `left_placeholder` | `validator_rejected`).
3. Add an Alembic migration.

**Done when:** a test creates a `MappingState`, saves, reloads, and asserts round-trip.

---

### Phase 2 — Deterministic pre-pass in job setup

**Goal:** turn a spreadsheet into a draft + sidecars with placeholders.

1. Extend `src/openscientist/job/setup.py` `create_job()` to accept a spreadsheet
   input and, after copying it into `job_dir/data/`, run a pre-pass that:
   - loads the sheet (pandas), infers columns and candidate slot guesses,
   - writes `draft_mapping.json` with **placeholders** for every unresolved
     column/value,
   - writes `curation_inputs.json` (per-column samples, headers, any provided
     study context),
   - writes `curation_report.json` skeleton (one row per placeholder).
2. Initialize `MappingState` from the draft.

**Done when:** running setup on a sample spreadsheet produces the three files and a
populated `MappingState`.

---

### Phase 3 — MCP tools (`openscientist_tools` subprocess)

**Goal:** swap the discovery toolset for a mapping toolset.

1. **Keep** `execute_code` (`code_exec.py`) — agent inspects the sheet and runs
   `runoak`.
2. **Remove** `search_pubmed` (`pubmed.py`) and `update_knowledge_state`
   (`knowledge.py`) from the registered tools.
3. **Add** schema tools (calling the Phase 0 module): `list_interfaces`,
   `get_slots(interface)`, `validate_value(slot, value)`.
4. **Add** ledger tools: `record_mapping(row, slot, value, evidence)` and
   `leave_placeholder(row, slot, reason)` — both persist to `MappingState` and
   update `curation_report.json`.
5. Update the system prompt's capability list in `prompts/` to match.

**Done when:** the tools subprocess starts and each tool round-trips against a real
DB (testcontainers), per repo testing conventions.

---

### Phase 4 — Skills: gut and reline

**Goal:** replace science methodology with mapping methodology.

1. Delete the science-domain skills under `skills/` (genomics, metabolomics, etc.).
2. Copy-in from ingest-agent into `skills/workflow/`:
   - `nmdc-curation-rules.md` (~verbatim).
   - `nmdc-env-triad.md`, `nmdc-taxon-resolution.md` — edit value-shape sections to
     submission-schema's `"label [CURIE]"` strings; keep judgment/refuse rules.
3. Author a new `spreadsheet-to-nmdc` orchestrator skill (workflow category),
   modeled on `ncbi-to-nmdc.md`'s step structure: review inputs → resolve
   placeholders per column → validate → write report.
4. Confirm `agent/skills.py` `write_skills_to_claude_dir()` materializes the new
   set (DB `Skill` rows updated/seeded accordingly).

**Done when:** a job workspace shows the new skills in `.claude/skills/` and none of
the old science skills.

---

### Phase 5 — Prompts & orchestrator

**Goal:** make the loop speak "resolve placeholders," not "generate hypotheses."

1. Rewrite the system-prompt body in `prompts/common.py` (capabilities +
   skills pointer) for the mapping task; keep the Claude/Codex fragment split.
2. Rewrite the iteration prompt builder in `orchestrator/iteration.py`
   (`build_iteration_prompt`) to summarize remaining placeholders from
   `MappingState` instead of KnowledgeState findings.
3. Leave `orchestrator/discovery.py` loop control (iteration count, outcome
   interpretation, retries) unchanged.

**Done when:** an iteration prompt for a job lists the outstanding placeholders and
the enabled skills.

---

### Phase 6 — Report phase: both outputs

**Goal:** emit the conformant artifact and the audit trail.

1. Repurpose the report phase in `orchestrator/discovery.py` to produce:
   - the **schema-conformant artifact** (filled spreadsheet/JSON validated against
     the submission schema), and
   - the **curation report** (from `MappingState` / `curation_report.json`).
2. Keep the existing "freshness guard" retry logic (re-ask if the file wasn't
   freshly written).

**Done when:** a completed job leaves both a validated artifact and a report in
`job_dir`, and the artifact passes `validate_value` for every non-placeholder slot.

---

### Phase 7 — Tests

Follow repo conventions (real DB via testcontainers, real model instances, tests in
submodules mirroring source, `browser` for NiceGUI sims):

1. `tests/schema/test_submission_schema.py` — SchemaView load, slot/enum lookup.
2. `tests/job/test_prepass.py` — spreadsheet → draft + sidecars + `MappingState`.
3. `tests/tools/test_mapping_tools.py` — schema + ledger MCP tools round-trip.
4. `tests/orchestrator/test_iteration_prompt.py` — prompt reflects placeholders.
5. `tests/orchestrator/test_report_phase.py` — both artifacts produced; validation.

---

## What we are explicitly NOT doing

- Not vendoring any Python from the suggestor (schema access is authored here on
  `linkml-runtime`).
- Not reusing the suggestor's one-shot pipeline, its `llm_client`, or its
  function-doc SKILL.md files.
- Not reusing OpenScientist's hypothesis/discovery skills, `search_pubmed`, or
  `update_knowledge_state`.
- Not targeting the full `nmdc-schema` (chose the submission schema); the copied
  ingest-agent skills' value-shape sections are edited rather than taken verbatim.

---

## Open risks / follow-ups

1. **Schema value-shape drift.** The copied env-triad/taxon skills assume the full
   schema's `ControlledIdentifiedTermValue`; editing to submission-schema strings is
   manual and needs review against real submission-schema slots.
2. **Interface/exclusion derivation.** We derive "which classes are interfaces" from
   schema metadata rather than copying the suggestor's list; validate our derivation
   matches the real submission-schema interface set.
3. **Ontology DB size in the image.** Prefetching ENVO + NCBITaxon sqlite inflates the
   agent image; consider a shared cache volume if size becomes a problem.
4. **Column inference quality.** The deterministic pre-pass's initial slot guesses set
   the agent's starting point; weak guesses mean more placeholders (safe but slower).
5. **DOI/PDF enrichment** (from the suggestor) is deferred; add later as an MCP tool
   only if source spreadsheets ship with associated publications.
