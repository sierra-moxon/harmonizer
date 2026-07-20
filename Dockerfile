# Dockerfile — the harmonizer web app image.
#
# Builds on harmonizer-base and installs the project via `uv sync`. Serves the
# NiceGUI UI (`python -m harmonizer.web`). This is the image docker-compose runs
# as the `web` service; it also owns the JobManager, which (when
# HARMONIZER_USE_CONTAINER_ISOLATION=1) launches sibling per-job containers via
# the mounted docker socket.
#
# Build:  docker build -f Dockerfile.base -t harmonizer-base:latest .
#         docker build -f Dockerfile      -t harmonizer-web:latest  .
FROM harmonizer-base:latest AS web

# --- dependency layer (cached unless the lockfile/manifest change) -----------
# Copy only the files that define the dependency set first so edits to source
# don't bust the (slow) dependency install layer.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# --- project layer -----------------------------------------------------------
COPY src ./src
COPY skills ./skills
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Job dirs live under the app tree (./jobs is bind-mounted at /app/jobs by
# docker-compose.yml). The database is Postgres in compose
# (HARMONIZER_DATABASE_URL is set there); a bare `docker run` falls back to the
# code default (local SQLite) via harmonizer.database.session.
ENV HARMONIZER_JOBS_ROOT=/app/jobs \
    PATH="/app/.venv/bin:${PATH}"

EXPOSE 8080

# Bind 0.0.0.0 inside the container so the published port is reachable.
CMD ["python", "-m", "harmonizer.web", "--host", "0.0.0.0", "--port", "8080"]
