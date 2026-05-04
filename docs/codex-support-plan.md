# Codex Support and Log Redaction Plan

> Roadmap for moving aide from Claude Code-only ingestion to provider-extensible
> AI coding session analysis, with redaction as a prerequisite for using real logs.

## Goal

Support Claude Code and Codex logs in one local aide database without weakening the
privacy model or forcing provider-specific details into the dashboard and autopsy
features.

The desired shape is:

```text
Claude logs -> provider parser -> normalized sessions -> SQLite -> dashboard/autopsy
Codex logs  -> provider parser -> normalized sessions -> SQLite -> dashboard/autopsy
```

The normalized model remains the contract. Provider parsers are adapters that
convert raw logs into `ParsedSession`, `ParsedMessage`, and `ToolCall`.

## Non-Goals

- Do not introduce LLM calls for parsing, categorization, or redaction.
- Do not upload logs or derived session contents to external services.
- Do not make Codex support a separate product or separate database.
- Do not require existing Claude users to change config before `aide ingest` works.
- Do not store raw prompts, raw tool output, or file contents in SQLite.

## Privacy and Redaction

Current Claude ingestion does not redact raw logs. It mostly avoids storing raw
message content in SQLite, but the archive behavior copies JSONL files as-is.
Before real Codex logs are checked into fixtures or shared in discussion, aide
needs a redaction tool.

### Redaction Scope

The redactor should preserve fields needed to develop and test parsers:

- JSONL line structure and valid JSON syntax.
- Event types, roles, timestamps, IDs, parent IDs, and session IDs.
- Model names, token counts, stop reasons, and usage metadata.
- Tool names, tool IDs, success/error markers, and command categories.
- File extensions and stable path shape when useful for metrics.
- String lengths where prompt or output length is useful.

The redactor should remove or replace sensitive data:

- User prompts and assistant prose.
- Tool outputs, file contents, diffs, and command stdout/stderr.
- Local usernames, home paths, and private project path segments.
- Secrets, API keys, bearer tokens, cookies, and authorization headers.
- Private URLs, query strings, hostnames, repository remotes, and email addresses.
- Environment variable values and shell arguments likely to contain secrets.

Use deterministic placeholders so tests are stable:

```text
/Users/brianliou/projects/aide/src/app.py -> /Users/<user>/projects/<project>/src/app.py
actual prompt text -> <redacted:text len=123>
tool output -> <redacted:tool_output len=2048>
secret value -> <redacted:secret>
```

### Redaction CLI

Add dedicated commands:

```bash
aide redact <path> --provider claude --out redacted.jsonl
aide redact <path> --provider codex --out redacted.jsonl
aide backup-redacted --out ~/.local/share/aide/redacted-logs
aide redact-audit ~/.local/share/aide/redacted-logs --strict
aide jobs status
```

Recommended behavior:

- Accept one file or a directory.
- For directories, preserve relative paths under the output directory.
- Never overwrite the source file.
- Print counts only: files processed, lines processed, fields redacted.
- Audit redacted outputs by printing finding categories and JSON field paths only,
  never matched raw values.
- Report routine launchd ingest and redacted-backup job health without printing
  raw session logs.
- Do not print raw lines or raw field values.
- Fail closed on unknown provider unless `--provider auto` is later added.

`aide backup-redacted` should read configured sources and write provider-separated
redacted backups, preserving relative JSONL paths.

## Provider Extensibility Surfaces

### Config

Current config has one `log_dir`, which implies Claude. Add a source list while
preserving backward compatibility:

```yaml
sources:
  - provider: claude
    path: ~/.claude/projects
  - provider: codex
    path: ~/.codex/sessions
```

Compatibility rule:

- If `sources` is absent, use `log_dir` as a single Claude source.
- If both are present, `sources` wins and `log_dir` is legacy.

### CLI

Extend ingestion without breaking current behavior:

```bash
aide ingest
aide ingest --provider claude
aide ingest --provider codex
```

`aide ingest` should mean all configured sources. With legacy config, that is
equivalent to Claude-only ingestion.

### Parser Boundary

Move provider-specific logic behind adapters:

```text
aide.parser              shared dispatch and normalized helpers
aide.parsers.claude      Claude discovery and parsing
aide.parsers.codex       Codex discovery and parsing
aide.redaction           provider-aware redaction
```

Each provider adapter should expose:

```python
def discover_files(root: Path) -> list[Path]: ...
def parse_file(path: Path) -> list[ParsedSession]: ...
def redact_file(input_path: Path, output_path: Path) -> RedactionResult: ...
```

### Session Identity

Raw session IDs may collide across providers. Use provider-qualified identity in
the database.

Schema direction:

- Add `provider TEXT NOT NULL DEFAULT 'claude'` to `sessions`.
- Add `provider` to `messages`, `tool_calls`, `work_blocks`, and `ingest_log`.
- Change session uniqueness to `(provider, session_id)`.
- Query child rows by both provider and session ID.

Autopsy and session detail URLs can stay simple at first only if ambiguity is
handled. Preferred long-term shape:

```text
/sessions/<provider>/<session_id>
aide autopsy --provider codex <session-id>
```

### Database Migrations

Migration requirements:

- Existing rows get `provider = 'claude'`.
- Existing Claude-only databases keep working after migration.
- Re-ingesting one provider/session deletes only child rows for that provider and
  session ID.
- `ingest_log` tracks `(provider, source_file)`.

### Tool Normalization

Dashboard and autopsy expect canonical tools like `Read`, `Edit`, `Write`,
`Bash`, `Grep`, and `Glob`. Codex raw tool names may differ.

Add a provider-aware normalization layer:

```text
raw provider tool -> canonical tool name -> category
```

Keep raw names only if needed later. Canonical names should drive existing charts
and effectiveness metrics.

### Cost and Tokens

Cost estimation is currently Claude-family oriented. Make cost provider-aware:

```python
estimate_cost(
    input_tokens,
    output_tokens,
    cache_read_tokens=0,
    cache_creation_tokens=0,
    model=model,
    provider=provider,
)
```

Codex/OpenAI token semantics may differ. Treat unavailable fields as unknown in
UI copy when semantics are not equivalent, rather than pretending they are zero.

### Effectiveness Metrics

Some current metrics are Claude-shaped:

- Cache hit rate.
- Compaction count.
- Permission mode.
- Thinking character count.

Provider-specific metrics should be nullable or marked unavailable. Cross-provider
charts should either use metrics with common semantics or visibly label provider
differences.

### Autopsy and Recommendations

Autopsy should consume normalized rows. Recommendation wording should become
provider-aware:

- Claude: `CLAUDE.md` suggestions.
- Codex: `AGENTS.md` or Codex instruction suggestions.
- Generic fallback: "project instructions".

## Roadmap

### Phase 0: Redaction First

- Done: add redaction module and `aide redact`.
- Done: add `aide backup-redacted` for routine provider-aware redacted backups.
- Done: add `aide redact-audit` to validate redacted outputs for likely sensitive
  leftovers.
- Done: add `aide jobs status` to summarize routine launchd ingest,
  effectiveness snapshot, and redacted-backup health.
- Done: add synthetic redaction tests for Claude and Codex shapes.
- Done: create redacted Codex fixtures from real local logs.
- Done: verify no sensitive strings remain in fixtures.

### Phase 1: Provider Foundation

- Done: add provider constants/types.
- Done: add provider fields and migrations.
- Done: update DB ingest and query helpers to use provider-qualified identity.
- Done: keep all existing Claude fixtures and tests passing.

### Phase 2: Claude Adapter Refactor

- Done: add explicit Claude provider adapter module.
- Done: keep `parse_jsonl_file` and `discover_jsonl_files` compatibility surface
  for tests and downstream imports.
- Done: keep default `aide ingest` behavior unchanged for legacy config.

### Phase 3: Config and CLI Sources

- Done: add `sources` config.
- Done: add `--provider` filtering.
- Done: raw archive is provider-separated when explicitly enabled, for example
  `archive/claude/...` and `archive/codex/...`.
- Done: add CLI tests for source selection and unknown providers.

### Phase 4: Codex Adapter

- Done: implement Codex discovery and parsing from redacted real-log fixtures.
- Done: normalize Codex messages, token usage, tool calls, file paths, commands, and
  errors into existing dataclasses.
- Done: add Codex/OpenAI cost support where token/model data supports it.

### Phase 5: UI and Autopsy Provider Awareness

- Done: show provider on sessions and session detail pages.
- Done: make model cost breakdown provider-aware.
- Done: update autopsy lookup, report header, cost analysis, and instruction
  suggestions for provider-qualified sessions.
- Done: add provider filters to project and session inspection pages.
- Done: add provider filters to overview, tools, and insights aggregate views.
- Done: use active work-block duration on project/session pages instead of
  wall-clock spans, which avoids unrealistic idle-time totals.
- Done: make session list semantics clearer with session labels, provider labels,
  active time, and signal chips.
- Done: normalize Codex command/workdir metadata enough to reduce fake project
  buckets and attribute shell-based edits.
- Done: expand error categories for Codex-heavy command failures, including
  file access, network, permission, external service, edit mismatch, lint, test,
  build, and git categories.
- Done: ensure provider-unavailable metrics do not crash aggregate pages.

### Phase 6: Documentation

- Update README, `AGENTS.md`, `CLAUDE.md`, config docs, and screenshots as needed.
- Document supported providers and unsupported/unknown metrics by provider.

### Phase 7: Follow-Up Insight Work

These are not blockers for current Codex support, but they are the next useful
work if we continue improving interpretation quality.

- Add an "Investigation Queue" insight panel that ranks suspicious sessions by
  high file-access failures, high edit mismatch count, zero active time, high
  cost per edit, no-edit tool sessions, and weak project attribution.
- Investigate remaining Codex `Other` errors. After the latest categorization
  pass, most Codex errors are classified, but a smaller residual bucket remains.
- Improve Codex file-change attribution beyond obvious shell mutations. Current
  parsing captures many edits from `apply_patch`, `sed -i`, redirects, `tee`, and
  Codex parsed command metadata, but richer shell workflows can still hide edits.
- Decide whether to store raw provider tool names alongside canonical tool names.
  This would make future parser/debugging work easier without weakening dashboard
  normalization.
- Add provider-specific metric availability notes in the UI for metrics that do
  not mean the same thing across Claude and Codex, such as compaction, thinking,
  turn timing, and token semantics.
- Add more redacted Codex fixtures that cover real failure shapes: sandbox
  denial, missing files, network failures, external CLI failures, patch mismatch,
  long-running shell sessions, and no-cwd/no-workdir logs.
- Consider a lightweight `aide inspect` or `aide data-quality` command that
  reports parser coverage issues without exposing raw log content.

## Current Wrap-Up Snapshot

As of 2026-05-02, aide has been generalized from Claude-only ingestion to a
provider-aware local effectiveness tool for Claude and Codex.

Completed:

- Provider-qualified schema and ingestion across sessions, messages, tool calls,
  work blocks, and ingest logs.
- Backward-compatible Claude defaults.
- Configured multi-source ingest for Claude and Codex.
- Provider-separated redacted backups and redaction audit.
- LaunchAgent status reporting for routine ingest and redacted backups.
- Claude and Codex parser adapters.
- Provider-aware cost estimation, dashboard filters, session detail, autopsy,
  tools, insights, and overview freshness.
- Active-time project/session summaries that avoid idle wall-clock inflation.
- Codex parser improvements for project fallback, permission normalization,
  shell normalization, file mutation attribution, and error categorization.
- Session detail guards for provider-reported turn waits that cross the
  30-minute idle threshold, so long human/permission gaps are not summarized as
  useful wait-time metrics.

Current local data after scoped Codex reingest:

- Claude: 332 sessions, 30 projects, 1304.2 active hours.
- Codex: 68 sessions, 17 projects, 93.2 active hours.
- Codex edit attribution: 263 edit calls, 228 with file paths.
- Codex error breakdown now exposes file access, git, network, build, external
  service, edit mismatch, lint, test, permission, and residual other categories.
- Latest full verification: `uv run ruff check src/ tests/` passed and
  `uv run pytest` passed with 468 tests.

## Acceptance Criteria

- Existing Claude ingestion works unchanged with legacy config.
- Existing databases migrate without data loss.
- Claude and Codex sessions with the same raw `session_id` can coexist.
- `aide ingest --provider claude` ingests only Claude sources.
- `aide ingest --provider codex` ingests only Codex sources.
- Mixed-provider dashboard pages render successfully.
- Session list and detail pages show provider.
- Autopsy works for Claude and Codex sessions.
- Missing provider-specific metrics are omitted or shown as unavailable, not as
  misleading zero values.
- Redacted fixtures are valid JSONL and parser-ingestable.
- Redacted fixtures contain no raw prompt text, tool output text, file contents,
  API keys, bearer tokens, local username, or private project paths.
- No new LLM calls are introduced.

## Testing Plan

### Redaction Tests

- Redacts prompt text and assistant prose.
- Redacts tool output and file contents.
- Redacts local home paths and usernames.
- Redacts secrets in environment variables, URLs, headers, and shell commands.
- Preserves valid JSONL.
- Preserves parser-required structure.
- Produces deterministic output.

### Parser Tests

- Claude adapter output matches current parser output on existing fixtures.
- Done: Codex adapter parses redacted Codex fixtures into normalized sessions.
- Done: Provider discovery returns only files for that provider.
- Done: Tool normalization maps raw provider tools to canonical tool names.
- Done: Codex token/model data produces provider-aware cost estimates.
- Unknown or unsupported events are ignored without failing the whole file.

### DB Tests

- Migration adds provider columns and backfills `claude`.
- `(provider, session_id)` uniqueness works.
- Child rows are keyed and deleted by provider plus session ID.
- `ingest_log` tracks provider plus source file.
- Daily stats and summary queries continue to aggregate correctly.

### CLI Tests

- Legacy `aide ingest` uses Claude `log_dir` when `sources` is absent.
- `--provider` filters configured sources.
- Unknown provider errors clearly.
- Archive paths are provider-separated.
- `aide backup-redacted` writes provider-separated redacted backups.
- `aide redact` prints counts only and does not print raw content.

### Web Tests

- Overview renders mixed Claude and Codex data.
- Sessions page shows provider.
- Session detail works for both providers.
- Provider filters return the expected rows.
- Provider-unavailable metrics render safely.

### End-to-End Smoke Test

- Done: ingest one redacted Claude fixture and one redacted Codex fixture into a
  temp DB.
- Done: run `aide stats`.
- Done: load `/`, provider-filtered aggregate pages, `/sessions`, and both
  provider-qualified session detail pages.
- Done: run `aide autopsy` for one Claude and one Codex session.

## Open Questions

- What is the stable on-disk Codex log location and filename pattern?
- Which Codex fields reliably contain model, token usage, and tool-call IDs?
- Should raw provider tool names be stored alongside canonical names?
- Should aide add redacted archive output as an ingest option, or keep redaction as a
  separate explicit command?
