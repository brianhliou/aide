# Justfile - Standard commands for aide
# Install just: https://github.com/casey/just#installation
# Run `just` to see available commands

# Default command - show help
default:
    @just --list

# Development
# ----------

# Start dashboard server
serve:
    uv run aide serve

# Run tests
test:
    uv run pytest tests/ -v

# Run tests in watch mode
test-watch:
    uv run pytest tests/ -v --watch

# Code Quality
# ------------

# Run linter
lint:
    uv run ruff check src/ tests/

# Format code
fmt:
    uv run ruff format src/ tests/

# Run all checks (lint + test)
check: lint test

# Data
# ----

# Ingest JSONL logs into SQLite
ingest:
    uv run aide ingest

# Ingest all logs (full rebuild)
ingest-full:
    uv run aide ingest --full

# Print summary stats
stats:
    uv run aide stats

# Setup
# -----

# Install dependencies
install:
    uv sync

# Setup project for first time
setup: install
    @echo "Project setup complete. Run 'just ingest' to parse your Claude Code logs."

# Clean build artifacts
clean:
    rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
