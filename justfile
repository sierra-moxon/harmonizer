# List available recipes
default:
    @just --list

# Sync dependencies (including dev group)
install:
    uv sync

# Run the test suite
test *args:
    uv run pytest {{args}}
