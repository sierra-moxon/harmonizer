# List available recipes
default:
    @just --list

# Sync dependencies (including dev group)
install:
    uv sync

# Run the test suite
test *args:
    uv run pytest {{args}}

# Run the deterministic pre-pass on a spreadsheet
prepass F *args:
    uv run python -m harmonizer.job.setup {{F}} {{args}}

# Start the MCP tools subprocess over stdio (manual smoke test)
tools:
    uv run python -m harmonizer_tools

# Materialize the workflow skills into a job dir's .claude/skills/ (smoke test)
skills dir=".":
    uv run python -c "from harmonizer.agent.skills import write_skills_to_claude_dir; print('\n'.join(str(p) for p in write_skills_to_claude_dir('{{dir}}')))"

# Run the orchestrator mapping loop for a prepared job dir
loop JOB_DIR:
    uv run python -m harmonizer.orchestrator {{JOB_DIR}}

# Serve the web UI (NiceGUI): TSV upload -> job -> results
web *args:
    uv run python -m harmonizer.web {{args}}

# Build the Docker image hierarchy: base -> web, and base -> executor -> agent
docker-build:
    docker build -f Dockerfile.base -t harmonizer-base:latest .
    docker build -f Dockerfile -t harmonizer-web:latest .
    docker build -f Dockerfile.executor -t harmonizer-executor:latest .
    docker build -f Dockerfile.agent -t harmonizer-agent:latest .

# Start the web app via docker-compose (UI at http://localhost:8080)
docker-up *args:
    docker compose up {{args}}
