# aide

> AI Developer Effectiveness toolkit. Track your AI coding productivity across all projects.

aide ingests your Claude Code session logs and gives you two tools to understand your AI usage: a **web dashboard** for long-term trends across projects, and **session autopsy** for deep-diving into individual sessions. The "Fitbit for AI coding."

## Why

The METR study found developers believe AI makes them 20% faster but were actually 19% slower. Without data, you're guessing. aide gives you the data.

## Quick Start

```bash
pip install aide-dashboard
aide ingest    # Parse your Claude Code logs into SQLite
aide serve     # Open the dashboard at localhost:8787
aide autopsy <session-id>   # Diagnose a specific session
```

## Two Tools, One Pipeline

aide has two distinct tools that share the same data pipeline:

```
~/.claude/projects/**/*.jsonl → parser → SQLite (aide.db) ─┬─→ Web Dashboard
                                                            └─→ Session Autopsy
```

### Web Dashboard (`aide serve`)

A local Flask dashboard showing trends across all your projects and sessions:

- **Cost tracking** — spend per day/week/month, per project, with 7-day moving average
- **Session browser** — list, filter by project, drill into any session's tool/token breakdown
- **Project comparison** — which projects consume the most, cost-per-session scatter plot
- **Tool usage** — which tools you use most, usage over time, most-accessed files

### Session Autopsy (`aide autopsy <session-id>`)

A per-session diagnostic report printed as Markdown. Analyzes a single session and produces four sections:

1. **Summary** — messages, tool calls, files modified/read, cost, tokens
2. **Cost Analysis** — cost broken down by category (file reads, code generation, execution, orchestration, overhead), cache efficiency, most expensive turns
3. **Context Analysis** — context window utilization curve, peak usage as % of 200K, compaction event detection with tokens-lost estimates
4. **CLAUDE.md Suggestions** — actionable recommendations based on session patterns (files read repeatedly, low cache hit rate, excessive compactions, high tool call count)

Example:

```bash
aide autopsy f982dfd8-65bd-4646-9272-16e0fb82f343
aide autopsy f982dfd8 > report.md   # Pipe to file
```

Both tools are heuristic-based. Zero LLM calls. Zero cost to run. All data stays local.

## Commands

```bash
aide ingest              # Parse new/changed logs
aide ingest --full       # Rebuild database from scratch
aide serve               # Start dashboard at localhost:8787
aide serve --port 9000   # Custom port
aide stats               # Print summary to terminal
aide autopsy <id>        # Diagnose a specific session
```

## Configuration

Optional config at `~/.config/aide/config.yaml`:

```yaml
# Set to true if you're on Claude Pro/Max subscription
# Costs will show as "estimated equivalent at API rates"
subscription_user: false
```

## Data Privacy

All data stays on your machine. No telemetry, no cloud, no accounts. aide reads local log files and stores results in a local SQLite database.

## Supported Tools

- **Claude Code** (v1) — full support via JSONL session logs
- **Cursor** (planned) — via local SQLite database

## Development

```bash
git clone https://github.com/brianhliou/aide.git
cd aide
uv sync          # Install dependencies
uv run pytest    # Run tests (192 tests)
uv run aide serve   # Start dev server
```

## License

MIT
