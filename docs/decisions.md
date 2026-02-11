# Decisions

> Append-only log of key decisions. Add new entries at the bottom.

---

**2026-02-10 — Project scope: portfolio-first open source**
Decided to build as a portfolio project, not a revenue play. Open source from day one. Monetization (WakaTime model) is a bonus if traction emerges, not the goal. This removes revenue risk as a concern and focuses on skill building + portfolio signal.

**2026-02-10 — Data source: Claude Code JSONL only (v1)**
Researched data access across AI tools. Claude Code has excellent local JSONL logs. Cursor has SQLite (deferred to v2). ChatGPT, Claude.ai, Gemini web have no local data — out of scope entirely. Windsurf and Copilot have no documented local logs.

**2026-02-10 — Database: SQLite as intermediate layer**
JSONL parsing on every page load would be slow as history grows. SQLite enables: incremental ingest (skip unchanged files), fast aggregation queries, pre-aggregated daily_stats, and a shared interface for future tools (Autopsy CLI, Cursor support).

**2026-02-10 — Web framework: Flask over FastAPI**
Dashboard is read-only — no async, no WebSockets, no complex forms. Flask is simpler for this use case. Jinja2 templating built in. FastAPI would add unnecessary complexity.

**2026-02-10 — Charting: Chart.js over D3**
D3 is too low-level for standard line/bar/pie charts. Chart.js has a high-level API with good defaults and loads via CDN (no build step). Plotly is too heavy.

**2026-02-10 — CLI name: `aide`**
"AI Developer Effectiveness" → aide. Short, memorable. `aide` is taken on PyPI (name-squatted at v0.0), so PyPI package is `aide-dashboard`. CLI command is still `aide`.

**2026-02-10 — Cost estimation: transparent about limitations**
Show API-rate cost estimates by default. For subscription users (Pro/Max), config flag changes display to "estimated equivalent at API rates" with explanatory badge. Accurate for API users, honestly approximate for everyone else.

**2026-02-10 — Zero LLM calls in v1**
All analysis is heuristic-based. No marginal cost to run. Session categorization (bug fix vs. feature) requires LLM and is deferred to future version.
