# Plan

> Architecture, trade-offs, and phased scope.

## Problem Space

### What problem are we solving?

Developers have no way to measure whether AI coding tools are actually making them more productive. The METR study found developers believe AI makes them 20% faster but were actually 19% slower. Enterprise tools exist ($50-200/dev/month) but track team-level metrics. WakaTime tracks time but not AI effectiveness. Without data, developers can't optimize their workflow, justify costs, or make informed decisions about which practices work.

### Who has this problem?

Individual developers using Claude Code who want to understand their own AI productivity and costs. Secondarily, Cursor users (v2). Tertiarily, anyone interested in the "AI ROI" question.

### What does success look like?

A local dashboard that loads in seconds, shows meaningful trends across 50+ sessions and 5+ projects, and produces at least one actionable insight that changes behavior (e.g., "project X costs 3x more per session").

## Architecture

### High-Level Design

```
~/.claude/projects/**/*.jsonl → JSONL Parser → SQLite (aide.db) → Flask + Chart.js Dashboard
```

Three CLI commands: `aide ingest` (parse logs), `aide serve` (start dashboard), `aide stats` (terminal summary).

### Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.12+ | Strongest skill, fast iteration, good stdlib SQLite/JSON |
| Database | SQLite | Zero deps, local-only tool, simple aggregations |
| Web | Flask + Jinja2 | Read-only dashboard, no async needed, simpler than FastAPI |
| Charts | Chart.js (CDN) | Simple API, good defaults, no build step |
| CSS | Tailwind (CDN) | Fast prototyping, no build tooling |
| CLI | Click | Standard Python CLI framework |
| Config | YAML | Human-readable, simple structure |

### Key Components

- **Parser** (`parser.py`): Reads JSONL line by line, groups by sessionId, extracts tokens/tools/timestamps
- **Database** (`db.py`): Schema (sessions, messages, tool_calls, daily_stats, ingest_log), upsert logic, aggregation queries
- **Cost estimator** (`cost.py`): API pricing formula, subscription-user flag
- **Web dashboard** (`web/`): Flask app with 4 pages (Overview, Projects, Sessions, Tools)

## Phases

### MVP (M1-M2)

- JSONL parser with incremental ingest
- SQLite database with session/message/tool_call tables
- CLI: `aide ingest`, `aide serve`, `aide stats`
- Web dashboard with Chart.js: cost trends, project comparison, session details, tool usage

### Next (M3)

- Dashboard polish, responsive layout, date range selectors
- "Effectiveness" indicators (cache hit rate, tokens per tool call)
- README, blog post, PyPI packaging, Show HN launch

### Out of Scope

- Cursor support (v2 expansion)
- LLM-powered analysis or session categorization
- Session Autopsy CLI
- User accounts, auth, cloud hosting
- Multi-user/team features
- Comparisons to other developers

## Open Questions

None — all design decisions are captured in the build brief.
