# Plan

> Architecture, trade-offs, and phased scope.

## Problem Space

### What problem are we solving?

Developers have no way to measure whether AI coding tools are actually making them more productive. The METR study found developers believe AI makes them 20% faster but were actually 19% slower. Enterprise tools exist ($50-200/dev/month) but track team-level metrics. WakaTime tracks time but not AI effectiveness. Without data, developers can't optimize their workflow, justify costs, or make informed decisions about which practices work.

### Who has this problem?

Individual developers using AI coding agents who want to understand their own AI productivity and costs. Claude Code and Codex are the first supported sources via provider-specific ingestion adapters. Tertiarily, anyone interested in the "AI ROI" question.

### What does success look like?

A local dashboard that loads in seconds, shows meaningful trends across 50+ sessions and 5+ projects, and produces at least one actionable insight that changes behavior (e.g., "project X costs 3x more per session").

## Architecture

### High-Level Design

```
provider logs -> provider parser -> normalized sessions -> SQLite (aide.db) -> Flask + Chart.js Dashboard
```

The initial provider is Claude Code via `~/.claude/projects/**/*.jsonl`. Codex support should use the same normalized session contract rather than adding a parallel product path. See `docs/codex-support-plan.md`.

Core CLI commands: `aide ingest` (parse logs), `aide serve` (start dashboard), `aide stats` (terminal summary), `aide autopsy` (diagnose a session). Planned provider work adds `aide redact` and provider filters for ingest/autopsy.

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

- **Parser** (`parser.py`, `claude_parser.py`, `codex_parser.py`): Reads provider logs through adapters, groups records into normalized sessions, extracts tokens/tools/timestamps
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

### Provider Extensibility

- Done: add log redaction before using real Codex logs as fixtures
- Done: add provider-qualified ingestion while preserving Claude defaults
- Done: split Claude parsing into an adapter
- Done: add Codex parsing from redacted real-log fixtures
- Done: make dashboard and autopsy provider-aware without duplicating product surfaces
- Next: improve Codex data-quality and investigation insights from normalized logs

### Semantic Compounding Ladder

The Claude/Codex ingestion substrate is now mostly built. The next frontier is
not more log plumbing; it is turning normalized session history into durable
artifacts that improve future agent sessions.

Principle:

```text
logs -> normalized events -> accepted artifacts -> future-agent context
```

Escalating milestones:

1. **Data-quality / investigation queue** - Rank sessions that need parser or
   interpretation review: high file-access failures, high edit mismatch count,
   no-edit sessions, weak project attribution, high cost per edit, residual
   `Other` errors. This keeps the existing metrics honest before adding new
   artifact generation.
2. **Semantic artifact schema** - Add a durable event/artifact layer above
   `sessions`, `messages`, and `tool_calls`. Initial artifact types:
   `decision`, `setup_step`, `credential_step`, `verification_recipe`,
   `agent_mistake`, `risky_action`, `future_agent_instruction`, and
   `planner_signal`.
3. **Session digest with proposed artifacts** - Add `aide digest` to summarize a
   session and propose durable artifacts. It should reuse existing structured
   logs first, and only introduce LLM analysis if the user explicitly chooses to
   relax the zero-LLM-call principle.
4. **Human accept/edit/skip review flow** - Add a lightweight review queue so
   aide does not pollute memory automatically. Accepted artifacts become the
   durable project knowledge base; skipped items remain session-local.
5. **Runbook generation** - Add `aide runbook` to generate or update project
   runbooks from accepted setup, credential, verification, and risk artifacts.
   Output should be Markdown that future agents and humans can read.
6. **Start-session brief** - Add `aide brief --project <name> --task <task>` to
   generate task-specific context packets from accepted artifacts and recent
   session history. This is the first visible compounding loop.
7. **Planner export** - Implement the `~/.aide/planner-export.json` contract so
   project-planner can use aide evidence during reviews: activity, cost/time
   drift, repeated blockers, verification quality, durable artifacts created,
   and projects with high or low AI leverage.
8. **Guard mode** - Add `aide guard check <command>` after runbooks exist. It
   should classify risky commands against known project setup, provider context,
   production/staging hints, backup evidence, and prior failure modes.

MVP cut:

```bash
aide digest --session latest
aide runbook generate --project <name>
aide brief --project <name> --task "<task>"
```

Success criterion: a future Claude/Codex session uses an aide-generated brief or
runbook and avoids at least one repeated setup investigation, wrong assumption,
or unsafe action.

### Out of Scope

- Cursor support (later expansion)
- LLM-powered analysis or session categorization
- User accounts, auth, cloud hosting
- Multi-user/team features
- Comparisons to other developers

## Open Questions

- Whether raw log archiving should remain default once redacted archiving exists
- Which provider-specific effectiveness metrics should be hidden, labelled, or
  separated when semantics differ across Claude and Codex
