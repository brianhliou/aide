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

**2026-05-01 — Provider extensibility: Codex support via adapters**
Codex support should be added as a second ingestion provider, not as a separate product or separate dashboard. Raw provider logs should be normalized into the existing session/message/tool-call model, with provider-qualified session identity to avoid collisions. Claude Code remains the backward-compatible default for existing config.

**2026-05-01 — Redaction before real-log fixtures**
Real Claude or Codex logs may contain prompts, file paths, tool outputs, commands, and secrets. Before using real Codex logs as fixtures or sharing examples, add deterministic redaction tooling that preserves parser-relevant structure while removing sensitive content. The product remains zero-LLM-call; redaction must be local and heuristic-based.

**2026-05-02 — Redaction policy: structure over prose**
Redacted backups and fixtures should preserve event shape, timestamps, provider metadata, model names, token usage, tool names, exit codes, durations, sanitized command structure, and path shape. They should remove prompt text, assistant prose, tool output, code/file contents, patch lines, image payloads, URLs, secrets, and local usernames/project names. Secret matching should target assignments, bearer values, known token formats, and CLI secret flags; it should not redact ordinary command vocabulary such as `modal token info` or phrases like "token context window."

**2026-05-02 — Codex support wrap-up: interpretation quality is the next frontier**
Claude and Codex now share provider-qualified ingestion, redacted backups, dashboard filters, session detail, autopsy, and aggregate insights. Codex parsing captures command/workdir project fallback, permission mode normalization, shell normalization, obvious file mutation attribution, and richer error categories. Remaining work should focus on interpretation quality rather than ingestion plumbing: data-quality checks, an investigation queue, remaining `Other` error analysis, richer shell edit attribution, and provider-specific metric availability notes.

**2026-05-02 — Active time is authoritative over wall-clock and provider wait time**
Project and session time should use active work-block duration, not raw wall-clock spans. Session detail may preserve wall-clock and provider-reported turn data for debugging, but provider turn waits over the 30-minute idle threshold should be treated as unreliable for productivity timing because they may include human sleep, permission waits, or other idle gaps.
