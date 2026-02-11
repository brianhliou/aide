# CLAUDE.md

> Instructions for Claude Code. Claude reads this file automatically.

## Project Overview

**aide** — AI Developer Effectiveness toolkit. Ingests Claude Code JSONL session logs into SQLite and provides two analysis tools:

1. **Web Dashboard** (`aide serve`) — Flask dashboard showing cost trends, token usage, session patterns, and project comparisons across all sessions. The "Fitbit for AI coding."
2. **Session Autopsy** (`aide autopsy <id>`) — Per-session diagnostic CLI that produces a Markdown report with cost breakdown, context window analysis, compaction detection, and CLAUDE.md improvement suggestions.

Both tools share the same ingestion pipeline and SQLite database. They are siblings — `aide.web` and `aide.autopsy` both import from core modules (`aide.db`, `aide.config`, `aide.cost`) but never from each other.

Build brief with full context, milestones, and design decisions: `~/projects/project-planner/briefs/ai-effectiveness-dashboard.md`

## Tech Stack

- **Language:** Python 3.12+
- **Package management:** uv + pyproject.toml
- **Web:** Flask + Jinja2 templates
- **Charts:** Chart.js (CDN)
- **CSS:** Tailwind CSS (CDN)
- **Database:** SQLite (stdlib sqlite3)
- **CLI:** Click
- **Config:** YAML (~/.config/aide/config.yaml)
- **Task runner:** justfile

## Project Structure

```
src/aide/
├── cli.py          # Click CLI: ingest, serve, stats, autopsy
├── parser.py       # JSONL → structured session data
├── db.py           # SQLite schema, queries, ingest
├── cost.py         # Cost estimation logic
├── config.py       # YAML config loading + defaults
├── models.py       # Shared dataclasses (ParsedSession, ParsedMessage, ToolCall)
├── web/
│   ├── app.py      # Flask app factory
│   ├── routes.py   # Route handlers
│   ├── queries.py  # SQL query functions for dashboard
│   ├── templates/  # Jinja2 templates (base, overview, projects, sessions, tools)
│   └── static/     # charts.js
├── autopsy/
│   ├── analyzer.py    # 4 analyzer functions + dataclasses
│   ├── queries.py     # DB queries for session analysis
│   ├── report.py      # Markdown report renderer
│   └── suggestions.py # CLAUDE.md suggestion rules engine
└── __main__.py     # python -m aide entrypoint
tests/
├── conftest.py
├── test_parser.py
├── test_db.py
├── test_cost.py
├── test_web.py
├── test_autopsy.py
└── fixtures/
    └── sample.jsonl
```

## Commands

Run `just` to see all available commands. Key commands:

- `just install` — install dependencies with uv
- `just test` — run pytest
- `just lint` — run ruff
- `just ingest` — parse JSONL logs into SQLite
- `just serve` — start dashboard at localhost:8787
- `uv run aide autopsy <session-id>` — generate session diagnostic report

## Key Patterns

- **Data flow:** `~/.claude/projects/**/*.jsonl` → parser → SQLite (aide.db) → web dashboard / autopsy CLI
- **Sibling architecture:** `aide.web` and `aide.autopsy` are independent sub-packages. Both read from the shared SQLite DB via core modules. Neither imports from the other.
- **Incremental ingest:** Track file mtime in `ingest_log` table, skip unchanged files
- **Zero LLM calls:** All analysis is heuristic-based, no marginal cost
- **Cost estimation:** API pricing by default, `subscription_user` flag for Pro/Max users shows "estimated equivalent" badge
- **Context window size:** `input_tokens + cache_read_tokens + cache_creation_tokens` per API call (not just `input_tokens`)
- **Project name derivation:** Extract from Claude log directory names (e.g., `-Users-brianliou-projects-slopfarm` → `slopfarm`)
- **Pre-aggregated stats:** `daily_stats` table materialized on each ingest for fast dashboard queries

## How to Work on This Project

1. Read the build brief: `~/projects/project-planner/briefs/ai-effectiveness-dashboard.md`
2. Check `git log --oneline -20` for recent changes
3. Find the current milestone (first with incomplete tasks in the brief)
4. Pick the next incomplete task and work on it
5. Run `just check` before committing
