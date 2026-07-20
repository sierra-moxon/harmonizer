# harmonizer

Iterative agent that maps arbitrary spreadsheets to the
[`nmdc-submission-schema`](https://github.com/microbiomedata/submission-schema).

A deterministic pre-pass drafts a mapping with placeholders for anything it
cannot resolve; an iterative agent loop then resolves each placeholder with
evidence (schema slot lookup, ontology resolution via OAK/`runoak`) or
explicitly refuses. Output is both a schema-conformant artifact and a curation
report auditing every placeholder outcome.

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the full implementation
plan (a NiceGUI web app + iterative agent, rebuilt fresh from OpenScientist
patterns). The earlier
[docs/SPREADSHEET_MAPPING_AGENT_PLAN.md](docs/SPREADSHEET_MAPPING_AGENT_PLAN.md)
is superseded.

---

## What problem does it solve?

Scientists submit environmental/microbiome data as **spreadsheets**
(TSV/CSV/Excel). A database called **NMDC** (National Microbiome Data
Collaborative) requires that data to follow a strict **schema** — rules about
what columns mean, what values are legal, and which controlled vocabularies
(ontologies) to use.

Manually reconciling a messy spreadsheet against that schema is tedious expert
work. Harmonizer automates it using an AI agent, and — crucially — it never
guesses silently. Every column it cannot confidently map becomes a
**placeholder** that either gets **resolved with evidence** or **explicitly
refused**. You get an audit trail.

In short: *spreadsheet in → (1) validated schema-conformant file + (2) a
"curation report" explaining every decision, out.*

---

## Core mental model: a two-phase pipeline

```
  Spreadsheet upload
         │
         ▼
 ┌─────────────────────────┐
 │ PHASE 1: Pre-Pass       │   Deterministic, fast, no AI.
 │ (job/setup.py)          │   Pure Python + pandas.
 │                         │
 │ • Read the spreadsheet  │
 │ • Guess which schema    │
 │   "interface" it is     │   (e.g. SoilInterface)
 │ • Fuzzy-match each      │
 │   column to a schema    │
 │   "slot" w/ confidence  │
 │ • Anything unsure →     │
 │   a PLACEHOLDER         │
 └───────────┬─────────────┘
             │  writes 3 JSON "sidecar" files + DB rows
             ▼
 ┌─────────────────────────┐
 │ PHASE 2: Agent Loop     │   Iterative, uses the LLM.
 │ (orchestrator/loop.py)  │   Runs on a background thread.
 │                         │
 │ repeat up to N times:   │
 │  • Show agent the       │
 │    remaining            │
 │    placeholders         │
 │  • Agent uses TOOLS to  │
 │    look up schema,      │
 │    resolve ontology     │
 │    terms, validate      │
 │  • Agent RESOLVES or    │
 │    REFUSES each one     │
 │  • Stop early when none │
 │    remain               │
 └───────────┬─────────────┘
             │
             ▼
   Two outputs: mapped_output.json  +  curation_report.json
```

Key design philosophy: **do the cheap deterministic work first, and only spend
the expensive/unpredictable AI on the hard leftovers.** The pre-pass is
guaranteed and reproducible; the agent only touches what the pre-pass flagged as
uncertain.

---

## Architecture, piece by piece

There are four cooperating layers/processes. The important thing is *who runs
what*:

### 1. The Web App (what you see in the browser)
- **Framework:** [NiceGUI](https://nicegui.io/) — a Python library for building
  reactive web UIs without JavaScript. It runs on top of FastAPI.
- **File:** `src/harmonizer/web/app.py`
- **Pages:**
  - `/` — list of all jobs with live status badges
  - `/new` — upload a spreadsheet, add optional "study context," set max iterations
  - `/job/<id>` — live progress of one job; download buttons when done
  - `/schema` — browse the NMDC schema interfaces and their slots
- The page **polls the database every 1.5 seconds** to show live progress — a
  simple alternative to websockets.

### 2. The Job Manager (the traffic cop)
- **File:** `src/harmonizer/job/manager.py`
- Holds a small **thread pool** (2 workers by default). When you upload a file it
  runs the pre-pass **synchronously** (so you wait a moment), then **submits** the
  slow agent loop to a background worker and immediately redirects you to the job
  page. This split keeps the UI responsive.

### 3. The Agent + its Tools (the "brain" — two processes talking)
The AI agent and the tools it uses are **two separate processes** that
communicate over **MCP (Model Context Protocol)**.

- **The agent** (`src/harmonizer/agent/claude_code_agent.py`) wraps the **Claude
  Agent SDK**. It is the thing that "thinks."
- **The tools server** (`src/harmonizer_tools/`) is a separate subprocess exposing
  a fixed menu of functions the agent is *allowed* to call:
  - `list_interfaces()`, `get_slots(interface)` — ask the schema what's valid
  - `validate_value(slot, value)` — check a value against schema rules (enums, patterns)
  - `record_mapping(...)` — commit a resolved mapping to the ledger
  - `leave_placeholder(...)` — explicitly refuse, with a reason
  - `execute_code(...)` — a sandboxed Python cell (pandas + ontology lookups via `runoak`)

Why separate them? **Safety and control.** The LLM can only call these vetted
tools; every action it takes is auditable. The LLM proposes, deterministic code
disposes.

### 4. The Schema layer (the source of truth)
- **File:** `src/harmonizer/schema/submission_schema.py`
- Thin wrappers over `linkml-runtime`, which reads the NMDC schema (distributed
  as YAML via the `nmdc-submission-schema` pip package). Only the schema YAML is
  reused; the Python wrappers are original.

### State that ties it together
- **A relational database** (`database/models.py`, SQLAlchemy): two models —
  `Job` (one row per upload, table `jobs`) and `PlaceholderRow` (one row per
  uncertain column, table `placeholder_rows`). Statuses like
  `PENDING/RUNNING/COMPLETED` and outcomes like
  `RESOLVED/LEFT_PLACEHOLDER/VALIDATOR_REJECTED` live here. The engine is chosen
  by `HARMONIZER_DATABASE_URL` (`database/session.py`): it defaults to a local
  **SQLite** file (`sqlite:///harmonizer.db`) for `just web`, and the Docker
  Compose stack points it at a first-class **Postgres** service instead (hence
  `psycopg` in the dependencies). Same schema either way; no ORM changes needed.
- **JSON "sidecar" files** in each job directory (`draft_mapping.json`,
  `curation_inputs.json`, `curation_report.json`). The database and these files
  are kept in sync. The loop has a "freshness guard" that checks the curation
  report's modified-time advances each turn, to confirm the agent is doing work.

### Skills (how the agent knows the domain rules)
- **Folder:** `skills/workflow/*.md` — plain markdown encoding expert methodology
  (resolving an environmental triad, resolving a taxon via NCBITaxon, curation
  governance rules). Copied into each job's `.claude/skills/` so the agent reads
  them as guidance. Knowledge as prose, not code.

### Docker (optional isolation — ignore this at first)
- A hierarchy of Dockerfiles (`base → web`, and `base → executor → agent`) lets
  each mapping job run inside its own throwaway container with prefetched offline
  ontologies. The `Settings` default is off (`use_container_isolation=False`), so
  local `just web` runs the loop in-process. The **Docker Compose** stack instead
  turns isolation **on** (`HARMONIZER_USE_CONTAINER_ISOLATION=1`) and adds a
  first-class **Postgres** service the web process and every sibling job container
  share. **For local learning, skip Docker entirely.**

---

## Repository structure

```
harmonizer/
├── pyproject.toml           # dependencies (managed by `uv`)
├── justfile                 # task shortcuts (like a Makefile)
├── README.md
├── src/
│   ├── harmonizer/          # the main application
│   │   ├── web/             # NiceGUI browser UI  ← entry point
│   │   ├── job/             # setup.py (pre-pass) + manager.py (thread pool)
│   │   ├── orchestrator/    # loop.py (agent iteration) + prompts.py
│   │   ├── agent/           # Claude Agent SDK wrapper + skills loader
│   │   ├── schema/          # linkml wrappers over NMDC schema
│   │   ├── state/           # MappingState (placeholder tracking)
│   │   ├── providers/       # anthropic/cborg backend dispatch (factory.py)
│   │   ├── database/        # SQLAlchemy models + session (SQLite or Postgres)
│   │   ├── job_container/   # optional Docker-per-job runner
│   │   └── settings.py      # config from env vars (HARMONIZER_* prefix)
│   └── harmonizer_tools/    # the MCP tools server (separate process)
├── skills/workflow/*.md     # domain methodology the agent reads
├── Dockerfile.*             # optional containerized execution
└── evaluations/             # phase-by-phase test reports
```

The `justfile` is the cheat sheet — it lists every runnable action. `just` is a
task runner (like `make`).

---

## Running locally

### Prerequisites
- [uv](https://docs.astral.sh/uv/) — a fast Python package/environment manager
- [just](https://just.systems/) — a command runner

Docker is **not** required for local experimentation.

### Steps

```sh
just install          # sync dependencies (== uv sync)
just test             # (optional) run the test suite
just web              # start the web UI (== uv run python -m harmonizer.web)
```

Then open **http://127.0.0.1:8080**, click **New**, upload a spreadsheet
(TSV/CSV/XLSX), and submit.

### The API key caveat

The system has two modes depending on whether you provide an LLM key:

- **Without an API key** — the deterministic **pre-pass and the whole UI work
  fine.** You can upload a file, watch it guess an interface, and see placeholders
  created. Best way to understand the plumbing without spending money. The agent
  loop cannot resolve placeholders (no brain to call).
- **With an API key** — the agent loop actually resolves/refuses placeholders:

```sh
export ANTHROPIC_API_KEY=sk-...
just web
```

### Choosing a provider

The backend is selected with `HARMONIZER_PROVIDER` (default `anthropic`). Two
providers ship today:

```sh
# Direct Anthropic (default) — uses the x-api-key scheme
export ANTHROPIC_API_KEY=sk-ant-...

# CBORG (LBNL gateway) — uses Claude Code's bearer scheme under the hood
export HARMONIZER_PROVIDER=cborg
export CBORG_API_KEY=...            # request one at https://cborg.lbl.gov/api_request
# optional overrides:
export HARMONIZER_MODEL=claude-opus-4-8                            # CBORG default
export HARMONIZER_CBORG_BASE_URL=https://api-local.cborg.lbl.gov   # LBL network only
```

Under the hood the CBORG provider points the Claude Code CLI at
`ANTHROPIC_BASE_URL` with an `ANTHROPIC_AUTH_TOKEN` bearer token, rather than an
`ANTHROPIC_API_KEY`. See [cborg.lbl.gov](https://cborg.lbl.gov/) for models.

### Useful config knobs (optional environment variables)

```sh
export ANTHROPIC_API_KEY=sk-...                      # enables the agent
export HARMONIZER_MAX_ITERATIONS=10                  # agent loop cap
export HARMONIZER_JOBS_ROOT=jobs                     # where job dirs go
export HARMONIZER_DATABASE_URL=sqlite:///harmonizer.db  # or postgresql+psycopg://…
export HARMONIZER_USE_CONTAINER_ISOLATION=0          # keep Docker off
```

`HARMONIZER_DATABASE_URL` accepts any SQLAlchemy URL: the SQLite file above for
local dev, or `postgresql+psycopg://user:pass@host:5432/db` to use Postgres (what
the Docker Compose stack sets automatically). Container-isolation deployments also
honor `HARMONIZER_JOB_IMAGE`, `HARMONIZER_HOST_PROJECT_DIR`,
`HARMONIZER_CONTAINER_APP_DIR`, and `HARMONIZER_AGENT_NETWORK` (see
`docker-compose.yml`).

### Poke at the parts individually

The `justfile` lets you run each stage in isolation:

```sh
just prepass path/to/your.tsv    # run ONLY the deterministic pre-pass, then
                                 # inspect the generated JSON sidecars
just loop <job-dir>              # run ONLY the agent loop on a prepared job
just tools                       # start the MCP tools server by hand (smoke test)
```

### Running via Docker (optional)

```sh
just docker-build    # build base -> web, base -> executor -> agent
just docker-up       # start Postgres + the web app; UI at http://localhost:8080
```

The Compose stack (`docker-compose.yml`) runs **Postgres 18** as a first-class
service and points `HARMONIZER_DATABASE_URL` at it
(`postgresql+psycopg://…@postgres:5432/…`), rather than the local SQLite file.
It also enables per-job container isolation by default, launching sibling job
containers via the mounted Docker socket.

---

## Suggested learning path

1. **Run `just web` with no API key**, upload a small TSV, and watch a job get
   created. Inspect the files under `jobs/<job-id>/` — especially
   `draft_mapping.json` and `curation_report.json`. This makes the "placeholder"
   concept concrete.
2. **Read `src/harmonizer/job/setup.py`** — the pre-pass. Plain, deterministic
   Python.
3. **Read `src/harmonizer/orchestrator/prompts.py`** — the exact text the agent is
   told and the tool menu it is given.
4. **Read `src/harmonizer_tools/server.py`** and the `*_tools.py` files — the
   vetted actions the agent can take.
5. **Then** trace `src/harmonizer/orchestrator/loop.py` to see how iterations,
   early-stop, and the freshness guard tie it together.
