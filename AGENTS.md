# Codex Instructions for aide

`aide` is an AI developer-effectiveness tool. It ingests Claude Code and Codex JSONL session logs into SQLite and analyzes cost trends, token usage, session patterns, efficiency metrics, and actionable recommendations.

## Workspace Coordination

- Follow `~/projects/MULTI_CODEX.md` for parallel Codex/Claude sessions.
- Prefer `just codex-status` for the local coordination preflight.
- Before implementation edits, check `git status --short --branch` and
  `git worktree list`.
- Use one branch and one worktree per implementation session; keep the primary
  checkout on `main` for integration unless explicitly assigned otherwise.

## Product Shape

- One product with multiple commands.
- Features are organized into layers, not separate projects.
- The differentiator is effectiveness: helping the user understand whether AI coding work is getting more efficient and where sessions go wrong.
- Keep the product zero-LLM-call unless the user explicitly chooses to change that principle.

## Layers

- Ingestion: get data in. Implemented by `aide ingest`.
- Descriptive: show what happened. Implemented by dashboard views.
- Diagnostic: explain what went wrong in a session. Implemented by `aide autopsy` and session detail pages.
- Effectiveness: show whether work is improving. This is the next important product layer.

## Tech Stack

- Python 3.12+.
- Package management: `uv` and `pyproject.toml`.
- Web: Flask and Jinja2.
- Charts: Chart.js from CDN.
- CSS: Tailwind CSS from CDN.
- Database: SQLite via stdlib `sqlite3`.
- CLI: Click.
- Config: YAML at `~/.config/aide/config.yaml`.

## Commands

```bash
just codex-status
uv run aide ingest
uv run aide ingest --provider codex
uv run aide ingest --full
uv run aide redact <path> --provider codex --out <path>
uv run aide backup-redacted
uv run aide redact-audit --strict
uv run aide jobs status
uv run aide serve
uv run aide stats
uv run aide autopsy <id>
uv run pytest
uv run ruff check src/ tests/
```

## Architecture Rules

- Claude data flow: `~/.claude/projects/**/*.jsonl` -> parser -> SQLite -> dashboard/autopsy.
- Codex data flow: `~/.codex/sessions/**/*.jsonl` -> Codex parser -> SQLite -> dashboard/autopsy.
- `aide.web` and `aide.autopsy` are features. They should consume shared core modules, not own shared logic.
- If code computes metrics or recommendations, put it in core modules unless duplication is not yet real.
- Keep thresholds as easy-to-find constants.
- Add metrics directly first; refactor only when duplication or coupling becomes concrete.

## Safety Rules

- Strictly disallow irreversible or secret-bearing operations in Codex context. Do not run them even if a permission prompt would allow it; instead explain the risk and give the user the exact command to run manually.
- Strictly disallowed operations include broad hard deletes (`rm -rf` outside clearly generated cache/dependency/build paths), `git clean`, `git reset --hard`, force pushes, branch/history rewrites, and commands that print or export secrets.
- Claude and Codex session logs may contain private prompts, file paths, and tool outputs. Treat them as sensitive.
- Do not print large raw logs into conversation unless the user asks.
- Do not send session-log contents to web search, MCP servers, or external tools.
- Do not enable raw log archiving unless the user explicitly asks; use `aide redact` for shareable fixtures, `aide backup-redacted` for routine redacted backups, and `aide redact-audit --strict` to validate redacted outputs.
- Do not rebuild or delete the SQLite database unless the user explicitly asks or the command is clearly scoped to the requested task.
