# CLAUDE.md

> Instructions for Claude Code. Claude reads this file automatically.

## Project Overview

**aide** — AI Developer Effectiveness tool. Ingests Claude Code JSONL session logs into SQLite and analyzes them: cost trends, token usage, session patterns, efficiency metrics, and actionable recommendations. The "Fitbit for AI coding."

aide is **one product** with multiple commands. Features are organized into layers, not separate projects.

Build brief: `~/projects/project-planner/briefs/ai-effectiveness-dashboard.md`

## Product Layers

Every feature in aide fits into one of these layers:

| Layer | Question it answers | Status |
|-------|-------------------|--------|
| **Ingestion** | "Get data in" | Done (`aide ingest`) |
| **Descriptive** | "What happened?" | Done (dashboard: overview, projects, sessions, tools) |
| **Diagnostic** | "What went wrong in this session?" | Done (`aide autopsy`, session detail page) |
| **Effectiveness** | "Am I getting better?" | Not built — this is the differentiator |

New features slot into a layer. They are not new "projects."

## Tech Stack

- **Language:** Python 3.12+
- **Package management:** uv + pyproject.toml
- **Web:** Flask + Jinja2 templates
- **Charts:** Chart.js (CDN)
- **CSS:** Tailwind CSS (CDN)
- **Database:** SQLite (stdlib sqlite3)
- **CLI:** Click
- **Config:** YAML (~/.config/aide/config.yaml)

## Project Structure

```
src/aide/
├── cli.py          # Click CLI: ingest, serve, stats, autopsy
├── parser.py       # JSONL → structured session data
├── db.py           # SQLite schema, queries, ingest
├── cost.py         # Cost estimation logic
├── config.py       # YAML config loading + defaults
├── models.py       # Shared dataclasses (ParsedSession, ParsedMessage, ToolCall)
├── web/            # Dashboard feature (descriptive layer)
│   ├── app.py      # Flask app factory
│   ├── routes.py   # Route handlers
│   ├── queries.py  # SQL query functions for dashboard
│   ├── templates/  # Jinja2 templates
│   └── static/     # charts.js
├── autopsy/        # Session diagnostics feature (diagnostic layer)
│   ├── analyzer.py    # 4 analyzer functions + dataclasses
│   ├── queries.py     # DB queries for session analysis
│   ├── report.py      # Markdown report renderer
│   └── suggestions.py # Recommendation rules engine
└── __main__.py     # python -m aide entrypoint
tests/
├── test_parser.py, test_db.py, test_cost.py
├── test_web.py     # 90 tests for dashboard
├── test_autopsy.py # 49 tests for diagnostics
└── fixtures/
```

## Commands

```bash
uv run aide ingest              # Parse new/changed logs
uv run aide ingest --full       # Rebuild database from scratch
uv run aide serve               # Start dashboard at localhost:8787
uv run aide stats               # Print summary to terminal
uv run aide autopsy <id>        # Diagnose a specific session
uv run pytest                   # Run all 192 tests
uv run ruff check src/ tests/   # Lint
```

## Key Patterns

- **Data flow:** `~/.claude/projects/**/*.jsonl` → parser → SQLite (aide.db) → dashboard / autopsy
- **Sub-packages are features, not products.** `aide.web` and `aide.autopsy` are both features of aide. They import from core modules (`aide.db`, `aide.config`, `aide.cost`) but not from each other.
- **Zero LLM calls:** All analysis is heuristic-based, no marginal cost
- **Context window size:** `input_tokens + cache_read_tokens + cache_creation_tokens` per API call
- **Incremental ingest:** Track file mtime in `ingest_log` table, skip unchanged files
- **Cost estimation:** API pricing by default, `subscription_user` flag shows "estimated equivalent" badge

## Adding New Features

1. Decide which layer the feature belongs to (descriptive / diagnostic / effectiveness)
2. If it computes a metric or recommendation, put the logic in a shared core module — not inside a sub-package
3. Sub-packages (`web/`, `autopsy/`) are consumers of shared logic, not owners of it
4. Metrics and recommendation thresholds should be constants at the top of the file — easy to find and tune

### Where shared vs feature-specific code goes

| Code type | Location |
|-----------|----------|
| Data models, parsing, ingestion | Core: `models.py`, `parser.py`, `db.py` |
| Cost estimation | Core: `cost.py` |
| Metric computation | Core: `metrics.py` (planned, currently in `autopsy/analyzer.py`) |
| Recommendation rules | Core: `recommendations.py` (planned, currently in `autopsy/suggestions.py`) |
| Dashboard UI | Feature: `web/` |
| Session diagnostics | Feature: `autopsy/` |

## Roadmap

### Phase 1: Effectiveness Foundations
Extract and generalize the metric/recommendation logic so it's shared and tunable.

1. **Extract shared metrics** — Move metric computation (cache efficiency, compaction detection, cost categorization) and recommendation rules out of `autopsy/` into shared core modules (`metrics.py`, `recommendations.py`). Both dashboard and autopsy consume them. Thresholds as named constants at the top of each file.
2. **Basic effectiveness metrics on dashboard** — Cache hit rate trends, efficiency scores per session, compaction rate over time. Surface diagnostic insights on the overview page (not just the autopsy CLI).
3. **Tune iteratively** — Thresholds and formulas will need ongoing adjustment. This is not a one-time task — expect to revisit as more session data accumulates.

### Phase 2: Per-Task Economics
The novel differentiator. Moves aide from "you spent $X" to "test-writing costs 3x more than implementation, here's why." Informed by the agent-session-economist exploration in project-planner.

4. **Task segmentation** — Segment sessions into tasks using heuristics: user message boundaries, temporal gaps, file clustering, git commit correlation. This is the hard problem. Start with the simplest heuristic (user message boundaries) and iterate.
5. **Per-task cost attribution** — Attribute token spend and cost to individual tasks within a session. Show which kinds of work (implementation, testing, debugging, exploration) cost the most.
6. **Optimization recommendations** — "Your test-writing sessions cost 3x more because you're reading 15 files per test. Add these to CLAUDE.md and save ~$X/month." Extends the shared `recommendations.py` with task-level rules.

### Phase 3: Polish + Launch
7. Dashboard polish — responsive layout, date range selector, subscription badge
8. README with screenshots
9. PyPI packaging (`pip install aide-dashboard`)
10. Blog post — METR study hook, personal findings
11. Show HN

### Evaluated and deferred
- **Context Continuity Engine** — Auto-extract decisions/patterns from sessions and generate CLAUDE.md content. Deferred because: requires LLM calls (breaks zero-cost principle), high platform risk (Anthropic shipping native Session Memory), different category (generation vs analysis). The heuristic parts (repeated-read suggestions) are already in autopsy. Revisit if Anthropic's native solution falls short.

## How to Work on This Project

1. Read this file and the build brief
2. Check `git log --oneline -20` for recent changes
3. Pick the next item from the roadmap
4. Run `uv run pytest && uv run ruff check src/ tests/` before committing
