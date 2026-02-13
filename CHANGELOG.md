# Changelog

## 0.1.0 (2026-02-11)

Initial release.

### Features

- **JSONL Parser** — Reads Claude Code session logs, extracts messages, token usage, tool calls, and session metadata
- **SQLite Database** — 6-table schema with incremental ingest (skips unchanged files via mtime tracking)
- **Cost Estimation** — API-rate pricing with subscription mode toggle for Pro/Max users
- **Work Blocks** — Splits sessions into continuous coding periods at 30-minute idle gaps for accurate duration tracking
- **Web Dashboard** (`aide serve`)
  - Overview: summary cards, effectiveness metrics, cost/time trends, token breakdown
  - Projects: per-project cost and session breakdown
  - Sessions: filterable session list with drill-in detail (tokens, tools, files, errors)
  - Tools: usage charts, error breakdown, most-accessed files, top bash commands
  - Insights: first-prompt analysis, cost concentration, time patterns, thinking blocks, tool sequences
- **Session Autopsy** (`aide autopsy`) — Per-session diagnostic report with cost breakdown and recommendations
- **CLI** — `aide ingest`, `aide serve`, `aide stats`, `aide autopsy`
- **Configuration** — YAML config at `~/.config/aide/config.yaml` with auto-detected defaults
