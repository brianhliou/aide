# aide

> AI Developer Effectiveness dashboard. Track your AI coding productivity across all projects.

aide ingests your Claude Code session logs and shows long-term trends — cost, token usage, session patterns, and efficiency metrics. The "Fitbit for AI coding."

## Why

The METR study found developers believe AI makes them 20% faster but were actually 19% slower. Without data, you're guessing. aide gives you the data.

## Quick Start

```bash
pip install aide-dashboard
aide ingest    # Parse your Claude Code logs
aide serve     # Open dashboard at localhost:8787
```

## What You Get

- **Cost tracking** — spend per day/week/month, per project, trending over time
- **Session analysis** — duration, token usage, message counts, tool usage breakdown
- **Project comparison** — which projects consume the most, which are most efficient
- **Usage patterns** — session frequency, time of day, tool preferences
- **Efficiency trends** — are you getting better at using AI over time?

## How It Works

```
~/.claude/projects/**/*.jsonl → parser → SQLite → web dashboard
```

aide reads Claude Code's local session logs (JSONL), parses them into a SQLite database, and serves a Chart.js dashboard. Zero LLM calls. Zero cost to run. All data stays local.

## Commands

```bash
aide ingest              # Parse new/changed logs
aide ingest --full       # Rebuild database from scratch
aide serve               # Start dashboard at localhost:8787
aide serve --port 9000   # Custom port
aide stats               # Print summary to terminal
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
git clone <repo-url>
cd aide
just setup    # Install dependencies
just test     # Run tests
just serve    # Start dev server
```

## License

MIT
