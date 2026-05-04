"""CLI entrypoint — aide ingest, aide serve, aide stats."""

from __future__ import annotations

import shutil
from pathlib import Path

import click

from aide.config import AideConfig, LogSource, load_config
from aide.db import (
    get_ingested_file,
    get_summary_stats,
    ingest_sessions,
    init_db,
    log_ingestion,
    rebuild_daily_stats,
)
from aide.jobs import collect_launchd_job_statuses, format_timestamp
from aide.providers import get_provider
from aide.redaction import SUPPORTED_PROVIDERS, audit_redacted_path, redact_path


@click.group()
def cli():
    """aide — local AI developer-effectiveness tooling."""
    pass


def archive_jsonl(file_path: Path, log_dir: Path, archive_dir: Path) -> None:
    """Copy a JSONL file to the archive directory, preserving relative path."""
    rel = file_path.relative_to(log_dir)
    dest = archive_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file_path, dest)


def resolve_ingest_sources(
    config: AideConfig,
    provider: str | None = None,
) -> list[LogSource]:
    """Return configured ingest sources, preserving legacy provider fallbacks."""
    if provider is None:
        return list(config.sources)

    matches = [source for source in config.sources if source.provider == provider]
    if matches:
        return matches
    if config.sources_configured:
        return []
    if provider == "codex":
        return [LogSource(provider="codex", path=config.codex_log_dir)]
    if provider == "claude":
        return [LogSource(provider="claude", path=config.log_dir)]
    raise ValueError(f"Unsupported provider: {provider}")


def ingest_source(
    db_path: Path,
    source: LogSource,
    full: bool,
    archive_raw: bool = False,
) -> dict:
    """Ingest one configured source and return counters for CLI output."""
    adapter = get_provider(source.provider)
    archive_dir = db_path.parent / "archive" / source.provider

    jsonl_files = adapter.discover_files(source.path)
    counters = {
        "provider": source.provider,
        "path": source.path,
        "files": len(jsonl_files),
        "ingested": 0,
        "skipped": 0,
        "archived": 0,
    }

    for file_path in jsonl_files:
        file_key = str(file_path)
        file_stat = file_path.stat()

        if not full:
            existing = get_ingested_file(db_path, file_key, provider=source.provider)
            if existing and existing["file_mtime"] == file_stat.st_mtime:
                counters["skipped"] += 1
                continue

        sessions = adapter.parse_file(file_path)
        if sessions:
            count = ingest_sessions(db_path, sessions)
            log_ingestion(
                db_path,
                file_key,
                file_stat.st_size,
                file_stat.st_mtime,
                count,
                provider=source.provider,
            )
            counters["ingested"] += count

        if archive_raw:
            archive_jsonl(file_path, source.path, archive_dir)
            counters["archived"] += 1

    return counters


def backup_redacted_sources(
    config: AideConfig,
    provider: str | None = None,
    output_dir: Path | None = None,
) -> list[dict]:
    """Redact configured sources into provider-separated backup directories."""
    backup_root = output_dir or (config.db_path.parent / "redacted-logs")
    results = []
    for source in resolve_ingest_sources(config, provider=provider):
        result = {
            "provider": source.provider,
            "files": 0,
            "lines": 0,
            "fields": 0,
            "invalid_lines": 0,
            "missing": False,
        }
        if not source.path.exists():
            result["missing"] = True
            results.append(result)
            continue

        redacted = redact_path(
            source.path,
            backup_root / source.provider,
            source.provider,
        )
        result.update({
            "files": redacted.files_processed,
            "lines": redacted.lines_processed,
            "fields": redacted.fields_redacted,
            "invalid_lines": redacted.invalid_lines,
        })
        results.append(result)

    return results


@cli.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--provider",
    type=click.Choice(sorted(SUPPORTED_PROVIDERS)),
    required=True,
    help="Log provider shape to redact.",
)
@click.option(
    "--out",
    "output_path",
    type=click.Path(path_type=Path),
    required=True,
    help="Output file or directory for redacted JSONL.",
)
def redact(path: Path, provider: str, output_path: Path):
    """Redact sensitive content from local provider JSONL logs."""
    try:
        result = redact_path(path, output_path, provider)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(
        f"Redacted {result.files_processed} files, "
        f"{result.lines_processed} lines, "
        f"{result.fields_redacted} fields."
    )
    if result.invalid_lines:
        click.echo(f"Skipped {result.invalid_lines} invalid JSONL lines.")


@cli.command("backup-redacted")
@click.option(
    "--provider",
    type=click.Choice(["claude", "codex"]),
    default=None,
    help="Only back up one provider.",
)
@click.option(
    "--out",
    "output_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Output directory. Default: aide data dir/redacted-logs.",
)
def backup_redacted(provider: str | None, output_dir: Path | None):
    """Write redacted backups for configured provider log sources."""
    config = load_config()
    results = backup_redacted_sources(
        config,
        provider=provider,
        output_dir=output_dir,
    )
    if not results:
        click.echo("No log sources configured.")
        return

    total_files = sum(r["files"] for r in results)
    total_lines = sum(r["lines"] for r in results)
    total_fields = sum(r["fields"] for r in results)
    click.echo(
        f"Backed up {total_files} redacted files, "
        f"{total_lines} lines, {total_fields} fields redacted."
    )
    for result in results:
        if result["missing"]:
            click.echo(f"{result['provider']}: source missing, skipped.")
        else:
            click.echo(
                f"{result['provider']}: {result['files']} files, "
                f"{result['lines']} lines."
            )


@cli.command("redact-audit")
@click.argument("path", required=False, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--provider",
    type=click.Choice(["claude", "codex"]),
    default=None,
    help="Audit one provider directory under the default redacted backup root.",
)
@click.option(
    "--max-string-length",
    type=int,
    default=500,
    show_default=True,
    help="Flag non-placeholder text fields longer than this many characters.",
)
@click.option(
    "--strict",
    is_flag=True,
    help="Exit non-zero when audit findings are present.",
)
def redact_audit(
    path: Path | None,
    provider: str | None,
    max_string_length: int,
    strict: bool,
):
    """Audit redacted JSONL output for likely sensitive leftovers."""
    config = load_config()
    audit_path = path or (config.db_path.parent / "redacted-logs")
    if provider:
        audit_path = audit_path / provider

    if not audit_path.exists():
        raise click.ClickException(f"Path does not exist: {audit_path}")

    result = audit_redacted_path(audit_path, max_string_length=max_string_length)
    click.echo(
        f"Audited {result.files_scanned} files, "
        f"{result.lines_scanned} lines. "
        f"Findings: {result.finding_count}."
    )
    if result.invalid_lines:
        click.echo(f"Invalid JSONL lines: {result.invalid_lines}.")

    if result.findings:
        for (kind, field_path), count in sorted(result.findings.items()):
            click.echo(f"{kind}: {count} at {field_path}")

    if strict and result.finding_count:
        raise click.ClickException("Redaction audit found possible sensitive leftovers.")


@cli.group()
def jobs():
    """Inspect local aide background jobs."""
    pass


@jobs.command("status")
@click.option(
    "--skip-audit",
    is_flag=True,
    help="Skip the redacted-backup audit check.",
)
def jobs_status(skip_audit: bool):
    """Show launchd status for routine aide background jobs."""
    config = load_config()
    statuses = collect_launchd_job_statuses()

    for status in statuses:
        icon = "ok" if status.healthy else "attention"
        click.echo(f"{status.name}: {icon}")
        click.echo(f"  label: {status.label}")
        click.echo(f"  plist: {status.plist_path} ({_present(status.plist_exists)})")
        click.echo(f"  loaded: {_yes_no(status.loaded)}")
        if status.error and not status.loaded:
            click.echo(f"  launchctl: {status.error}")
        click.echo(f"  state: {status.state or 'unknown'}")
        click.echo(f"  schedule: {status.schedule or 'unknown'}")
        click.echo(f"  runs: {_value_or_unknown(status.runs)}")
        click.echo(f"  last exit code: {_value_or_unknown(status.last_exit_code)}")

        if status.stdout_path:
            click.echo(f"  stdout: {status.stdout_path}")
            click.echo(f"  stdout updated: {format_timestamp(status.stdout_updated_at)}")
            for line in status.stdout_last_lines or []:
                click.echo(f"  last output: {line}")
        else:
            click.echo("  stdout: unknown")

        if status.stderr_path:
            size = status.stderr_size
            size_text = "missing" if size is None else f"{size} bytes"
            click.echo(f"  stderr: {status.stderr_path} ({size_text})")
            click.echo(f"  stderr updated: {format_timestamp(status.stderr_updated_at)}")
        else:
            click.echo("  stderr: unknown")

    backup_root = config.db_path.parent / "redacted-logs"
    if skip_audit:
        click.echo("redaction audit: skipped")
        return
    if not backup_root.exists():
        click.echo(f"redaction audit: missing ({backup_root})")
        return

    audit = audit_redacted_path(backup_root)
    status_text = "pass" if audit.finding_count == 0 else "attention"
    click.echo(
        f"redaction audit: {status_text} "
        f"({audit.files_scanned} files, {audit.lines_scanned} lines, "
        f"{audit.finding_count} findings)"
    )
    if audit.findings:
        for (kind, field_path), count in sorted(audit.findings.items()):
            click.echo(f"  {kind}: {count} at {field_path}")


@cli.command()
@click.option("--full", is_flag=True, help="Rebuild database from scratch (re-parse all files).")
@click.option(
    "--source",
    type=click.Choice(["claude", "codex"]),
    default=None,
    help="Deprecated alias for --provider.",
)
@click.option(
    "--provider",
    type=click.Choice(["claude", "codex"]),
    default=None,
    help="Which local AI coding logs to parse.",
)
@click.option(
    "--archive-raw",
    is_flag=True,
    help="Copy raw JSONL logs into aide's archive directory. Sensitive; off by default.",
)
def ingest(
    full: bool,
    source: str | None,
    provider: str | None,
    archive_raw: bool,
):
    """Parse local AI coding JSONL logs into SQLite."""
    provider_filter = provider or source
    config = load_config()
    db_path = config.db_path

    init_db(db_path)

    sources = resolve_ingest_sources(config, provider=provider_filter)
    if not sources:
        click.echo("No log sources configured.")
        return

    results = [
        ingest_source(db_path, item, full=full, archive_raw=archive_raw)
        for item in sources
    ]
    rebuild_daily_stats(db_path)

    total_ingested = sum(r["ingested"] for r in results)
    total_files = sum(r["files"] for r in results)
    total_skipped = sum(r["skipped"] for r in results)
    total_archived = sum(r["archived"] for r in results)

    if total_files == 0:
        paths = ", ".join(str(item.path) for item in sources)
        click.echo(f"No JSONL files found in {paths}")
        return

    click.echo(f"Ingested {total_ingested} sessions from {total_files - total_skipped} files.")
    for result in results:
        if result["files"] == 0:
            click.echo(f"{result['provider']}: no JSONL files found in {result['path']}")
        else:
            click.echo(
                f"{result['provider']}: {result['ingested']} sessions, "
                f"{result['files'] - result['skipped']} files"
            )
    if total_skipped:
        click.echo(f"Skipped {total_skipped} unchanged files.")
    if total_archived:
        click.echo(f"Archived {total_archived} log files to {db_path.parent / 'archive'}")


@cli.command()
@click.option("--project", default=None, help="Show stats for a specific project.")
def stats(project: str | None):
    """Print summary statistics to terminal."""
    config = load_config()
    db_path = config.db_path

    if not db_path.exists():
        click.echo("No data yet. Run 'aide ingest' first.")
        return

    summary = get_summary_stats(db_path)

    if summary["total_sessions"] == 0:
        click.echo("No sessions found. Run 'aide ingest' first.")
        return

    date_range = summary["date_range"]
    cost_label = "est. cost" if config.subscription_user else "cost"

    click.echo(
        f"{summary['total_sessions']} sessions across "
        f"{summary['total_projects']} projects. "
        f"Total {cost_label}: ${summary['total_cost']:.2f}. "
        f"Date range: {date_range['min'][:10]} to {date_range['max'][:10]}."
    )

    if project:
        match = [
            p for p in summary["sessions_by_project"] if p["project_name"] == project
        ]
        if match:
            p = match[0]
            click.echo(
                f"\n{p['project_name']}: {p['session_count']} sessions, "
                f"${p['total_cost']:.2f} {cost_label}"
            )
        else:
            click.echo(f"\nNo data for project '{project}'.")
    else:
        click.echo("\nBy project:")
        for p in summary["sessions_by_project"]:
            click.echo(
                f"  {p['project_name']}: {p['session_count']} sessions, "
                f"${p['total_cost']:.2f}"
            )


@cli.command()
@click.option("--port", default=None, type=int, help="Port to serve on (default: 8787).")
def serve(port: int | None):
    """Start the dashboard web server."""
    config = load_config()
    serve_port = port or config.port

    if not config.db_path.exists():
        click.echo("No data yet. Run 'aide ingest' first.")
        return

    click.echo(f"Starting dashboard at http://localhost:{serve_port}")
    click.echo("Press Ctrl+C to stop.")

    try:
        from aide.web.app import create_app

        app = create_app(config)
        app.run(host="localhost", port=serve_port)
    except ImportError:
        click.echo("Web dashboard dependencies not found. Reinstall aide-dashboard.")


@cli.group()
def effectiveness():
    """Persist and inspect effectiveness trend snapshots."""
    pass


@effectiveness.command("snapshot")
@click.option(
    "--date",
    "snapshot_date",
    default=None,
    help="Snapshot date in YYYY-MM-DD format. Default: today.",
)
@click.option(
    "--window-days",
    default=30,
    show_default=True,
    type=click.IntRange(min=1),
    help="Lookback window to summarize.",
)
def effectiveness_snapshot(snapshot_date: str | None, window_days: int):
    """Persist today's all/provider/project effectiveness metrics."""
    config = load_config()
    db_path = config.db_path

    if not db_path.exists():
        click.echo("No data yet. Run 'aide ingest' first.", err=True)
        raise SystemExit(1)

    from datetime import date

    from aide.effectiveness import snapshot_effectiveness

    day = None
    if snapshot_date is not None:
        try:
            day = date.fromisoformat(snapshot_date)
        except ValueError as exc:
            raise click.ClickException("--date must use YYYY-MM-DD.") from exc

    rows = snapshot_effectiveness(db_path, snapshot_date=day, window_days=window_days)
    scopes = _count_by_scope(rows)
    effective_date = rows[0].snapshot_date if rows else (day or date.today()).isoformat()
    click.echo(
        f"Stored {len(rows)} effectiveness snapshot row(s) "
        f"for {effective_date} ({window_days}d)."
    )
    click.echo(
        "Scopes: "
        f"all={scopes.get('all', 0)}, "
        f"provider={scopes.get('provider', 0)}, "
        f"project={scopes.get('project', 0)}"
    )


@effectiveness.command("history")
@click.option(
    "--limit",
    default=20,
    show_default=True,
    type=click.IntRange(min=1),
    help="Maximum rows to show.",
)
def effectiveness_history(limit: int):
    """List recent persisted effectiveness snapshots."""
    config = load_config()
    db_path = config.db_path

    if not db_path.exists():
        click.echo("No data yet. Run 'aide ingest' first.", err=True)
        raise SystemExit(1)

    from aide.db import init_db
    from aide.effectiveness import ALL_VALUE, list_effectiveness_snapshots

    init_db(db_path)
    rows = list_effectiveness_snapshots(db_path, limit=limit)
    if not rows:
        click.echo("No effectiveness snapshots found.")
        return

    for row in rows:
        target = row["project_name"]
        if row["scope"] == "all":
            target = "all"
        elif row["project_name"] == ALL_VALUE:
            target = row["provider"]
        else:
            target = f"{row['provider']}/{row['project_name']}"
        click.echo(
            f"{row['snapshot_date']} {row['scope']} {target}: "
            f"{row['session_count']} sessions, "
            f"review {row['review_rate']:.0%}, "
            f"errors {row['error_rate']:.0%}, "
            f"attribution {row['edit_attribution_rate']:.0%}"
        )


def _count_by_scope(rows: list[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        scope = getattr(row, "scope")
        counts[scope] = counts.get(scope, 0) + 1
    return counts


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _present(value: bool) -> str:
    return "present" if value else "missing"


def _value_or_unknown(value: object | None) -> object:
    return value if value is not None else "unknown"


@cli.command()
@click.argument("session_id")
@click.option(
    "--provider",
    type=click.Choice(sorted(SUPPORTED_PROVIDERS)),
    default=None,
    help="Disambiguate the session provider when raw session IDs collide.",
)
def autopsy(session_id: str, provider: str | None):
    """Analyze a single session and produce a diagnostic report."""
    config = load_config()
    db_path = config.db_path

    if not db_path.exists():
        click.echo("No data yet. Run 'aide ingest' first.", err=True)
        raise SystemExit(1)

    from aide.autopsy.analyzer import (
        analyze_context,
        analyze_cost,
        analyze_suggestions,
        analyze_summary,
    )
    from aide.autopsy.queries import (
        get_session,
        get_session_files_touched,
        get_session_messages,
        get_session_tool_calls,
        get_session_tool_usage,
    )
    from aide.autopsy.report import render_report

    session = get_session(db_path, session_id, provider=provider)
    if session is None:
        click.echo(f"Session '{session_id}' not found.", err=True)
        raise SystemExit(1)
    provider = session["provider"]

    messages = get_session_messages(db_path, session_id, provider=provider)
    tool_calls = get_session_tool_calls(db_path, session_id, provider=provider)
    tool_usage = get_session_tool_usage(db_path, session_id, provider=provider)
    files_touched = get_session_files_touched(db_path, session_id, provider=provider)

    summary = analyze_summary(session, tool_usage, files_touched)
    cost_analysis = analyze_cost(session, messages, tool_calls)
    context = analyze_context(messages)
    suggestions = analyze_suggestions(
        files_touched,
        cost_analysis.cache_efficiency,
        context.estimated_compaction_count,
        session["tool_call_count"],
        provider=provider,
    )

    report = render_report(summary, cost_analysis, context, suggestions)
    click.echo(report)


@cli.command()
@click.argument("session_id")
@click.option(
    "--provider",
    type=click.Choice(sorted(SUPPORTED_PROVIDERS)),
    default=None,
    help="Disambiguate the session provider when raw session IDs collide.",
)
@click.option(
    "--save-proposals",
    is_flag=True,
    help="Persist generated proposals for later review. Default is preview only.",
)
def digest(session_id: str, provider: str | None, save_proposals: bool):
    """Propose reviewable semantic artifacts from one session."""
    config = load_config()
    db_path = config.db_path

    if not db_path.exists():
        click.echo("No data yet. Run 'aide ingest' first.", err=True)
        raise SystemExit(1)

    from aide.digest import build_digest, save_digest_proposals

    result = build_digest(db_path, session_id, provider=provider)
    if result is None:
        click.echo(f"Session '{session_id}' not found.", err=True)
        raise SystemExit(1)

    session = result.session
    qualified_id = f"{session['provider']}:{session['session_id']}"
    click.echo(f"Digest proposals for {qualified_id} ({session['project_name']})")

    if not result.proposals:
        click.echo("No artifact proposals found.")
        return

    artifact_ids = (
        save_digest_proposals(db_path, result) if save_proposals else [None] * len(result.proposals)
    )
    if save_proposals:
        click.echo(f"Saved {len(result.proposals)} artifact proposal(s).")
    else:
        click.echo(
            f"Dry run: {len(result.proposals)} artifact proposal(s). "
            "Use --save-proposals to persist them."
        )

    for index, (proposal, artifact_id) in enumerate(
        zip(result.proposals, artifact_ids, strict=True),
        start=1,
    ):
        artifact = proposal.artifact
        saved_suffix = f" #{artifact_id}" if artifact_id is not None else ""
        click.echo("")
        click.echo(f"{index}. [{artifact.artifact_type}]{saved_suffix} {artifact.title}")
        click.echo(f"   Confidence: {artifact.confidence}")
        click.echo(f"   {artifact.body}")
        if proposal.reason:
            click.echo(f"   Reason: {proposal.reason}")
        for evidence in proposal.evidence:
            click.echo(f"   Evidence: {evidence.summary}")


@cli.group()
def actions():
    """Propose artifacts from repeated investigation actions."""
    pass


@actions.command("propose")
@click.option("--signal", required=True, help="Investigation action slug, e.g. no-edits.")
@click.option(
    "--provider",
    type=click.Choice(sorted(SUPPORTED_PROVIDERS)),
    default=None,
    help="Filter by provider.",
)
@click.option("--project", "project_name", default=None, help="Filter by project name.")
@click.option("--hours", default=30 * 24, type=int, help="Lookback window in hours.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview proposals without saving them.",
)
def actions_propose(
    signal: str,
    provider: str | None,
    project_name: str | None,
    hours: int,
    dry_run: bool,
):
    """Create proposed artifacts from an investigation action signal."""
    config = load_config()
    db_path = config.db_path

    if not db_path.exists():
        click.echo("No data yet. Run 'aide ingest' first.", err=True)
        raise SystemExit(1)

    from aide.actions import build_action_proposals, save_action_proposals

    result = build_action_proposals(
        db_path,
        signal,
        provider=provider,
        project_name=project_name,
        hours=hours,
    )
    click.echo(f"Action proposals for {result.signal_label} ({hours}h)")
    if result.skipped_existing:
        click.echo(f"Skipped {result.skipped_existing} existing proposal(s).")

    if not result.proposals:
        click.echo("No new artifact proposals found.")
        return

    artifact_ids = (
        [None] * len(result.proposals)
        if dry_run else save_action_proposals(db_path, result)
    )
    if dry_run:
        click.echo(
            f"Dry run: {len(result.proposals)} artifact proposal(s). "
            "Run without --dry-run to persist them."
        )
    else:
        click.echo(f"Saved {len(result.proposals)} artifact proposal(s).")

    for index, (proposal, artifact_id) in enumerate(
        zip(result.proposals, artifact_ids, strict=True),
        start=1,
    ):
        artifact = proposal.artifact
        saved_suffix = f" #{artifact_id}" if artifact_id is not None else ""
        click.echo("")
        click.echo(f"{index}. [{artifact.artifact_type}]{saved_suffix} {artifact.title}")
        click.echo(f"   Project: {artifact.project_name}")
        click.echo(f"   Confidence: {artifact.confidence}")
        click.echo(f"   {artifact.body}")
        if proposal.reason:
            click.echo(f"   Reason: {proposal.reason}")
        for evidence in proposal.evidence:
            click.echo(f"   Evidence: {evidence.summary}")


@cli.group()
def artifacts():
    """Review proposed semantic artifacts."""
    pass


@artifacts.command("list")
@click.option("--project", "project_name", default=None, help="Filter by project name.")
@click.option(
    "--status",
    type=click.Choice(["proposed", "accepted", "rejected", "superseded", "archived"]),
    default=None,
    help="Filter by artifact status.",
)
@click.option(
    "--type",
    "artifact_type",
    type=click.Choice([
        "agent_mistake",
        "credential_step",
        "decision",
        "future_agent_instruction",
        "planner_signal",
        "risky_action",
        "setup_step",
        "verification_recipe",
    ]),
    default=None,
    help="Filter by artifact type.",
)
def artifacts_list(
    project_name: str | None,
    status: str | None,
    artifact_type: str | None,
):
    """List semantic artifacts."""
    config = load_config()
    db_path = config.db_path
    if not db_path.exists():
        click.echo("No data yet. Run 'aide ingest' first.", err=True)
        raise SystemExit(1)

    from aide.artifacts import list_artifacts

    rows = list_artifacts(
        db_path,
        project_name=project_name,
        status=status,
        artifact_type=artifact_type,
    )
    if not rows:
        click.echo("No artifacts found.")
        return

    for row in rows:
        source = _artifact_source_label(row)
        click.echo(
            f"#{row['id']} [{row['status']}] {row['artifact_type']} "
            f"{row['project_name']} - {row['title']}"
        )
        if source:
            click.echo(f"  source: {source}")


@artifacts.command("show")
@click.argument("artifact_id", type=int)
def artifacts_show(artifact_id: int):
    """Show one artifact with evidence and lifecycle events."""
    config = load_config()
    db_path = config.db_path
    if not db_path.exists():
        click.echo("No data yet. Run 'aide ingest' first.", err=True)
        raise SystemExit(1)

    from aide.artifacts import get_artifact

    artifact = get_artifact(db_path, artifact_id)
    if artifact is None:
        click.echo(f"Artifact #{artifact_id} not found.", err=True)
        raise SystemExit(1)

    _echo_artifact_detail(artifact)


@artifacts.command("accept")
@click.argument("artifact_id", type=int)
@click.option("--note", default=None, help="Optional review note.")
def artifacts_accept(artifact_id: int, note: str | None):
    """Accept a proposed artifact."""
    _review_artifact(artifact_id, "accepted", note=note)


@artifacts.command("reject")
@click.argument("artifact_id", type=int)
@click.option("--note", default=None, help="Optional review note.")
def artifacts_reject(artifact_id: int, note: str | None):
    """Reject a proposed artifact."""
    _review_artifact(artifact_id, "rejected", note=note)


def _review_artifact(artifact_id: int, status: str, note: str | None = None) -> None:
    config = load_config()
    db_path = config.db_path
    if not db_path.exists():
        click.echo("No data yet. Run 'aide ingest' first.", err=True)
        raise SystemExit(1)

    from aide.artifacts import accept_artifact, reject_artifact

    try:
        artifact = (
            accept_artifact(db_path, artifact_id, note=note)
            if status == "accepted"
            else reject_artifact(db_path, artifact_id, note=note)
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Artifact #{artifact_id} {artifact['status']}: {artifact['title']}")


def _echo_artifact_detail(artifact: dict) -> None:
    click.echo(f"Artifact #{artifact['id']}: {artifact['title']}")
    click.echo(f"Type: {artifact['artifact_type']}")
    click.echo(f"Status: {artifact['status']}")
    click.echo(f"Confidence: {artifact['confidence']}")
    click.echo(f"Project: {artifact['project_name']}")
    source = _artifact_source_label(artifact)
    if source:
        click.echo(f"Source: {source}")
    click.echo("")
    click.echo(artifact["body"])

    if artifact["evidence"]:
        click.echo("")
        click.echo("Evidence")
        for item in artifact["evidence"]:
            label = item["evidence_kind"]
            tool = f" via {item['tool_name']}" if item["tool_name"] else ""
            click.echo(f"- {label}{tool}: {item['summary']}")

    if artifact["events"]:
        click.echo("")
        click.echo("Events")
        for event in artifact["events"]:
            note = f" - {event['note']}" if event["note"] else ""
            click.echo(f"- {event['event_type']} at {event['created_at']}{note}")


def _artifact_source_label(artifact: dict) -> str | None:
    provider = artifact.get("source_provider")
    session_id = artifact.get("source_session_id")
    if not provider or not session_id:
        return None
    return f"{provider}:{session_id}"


@cli.group()
def runbook():
    """Generate runbooks from accepted artifacts."""
    pass


@runbook.command("generate")
@click.option(
    "--project",
    "project_name",
    required=True,
    help="Project name to generate a runbook for.",
)
@click.option(
    "--out",
    "output_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Write Markdown to this file instead of stdout.",
)
def runbook_generate(project_name: str, output_path: Path | None):
    """Generate a deterministic Markdown runbook for a project."""
    config = load_config()
    db_path = config.db_path
    if not db_path.exists():
        click.echo("No data yet. Run 'aide ingest' first.", err=True)
        raise SystemExit(1)

    from aide.runbook import render_project_runbook, write_project_runbook

    if output_path is None:
        click.echo(render_project_runbook(db_path, project_name), nl=False)
        return

    write_project_runbook(db_path, project_name, output_path)
    click.echo(f"Wrote runbook to {output_path}")


@cli.command()
@click.option(
    "--project",
    "project_name",
    required=True,
    help="Project name to generate a brief for.",
)
@click.option(
    "--task",
    required=True,
    help="Task description for the next agent session.",
)
@click.option(
    "--out",
    "output_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Write Markdown to this file instead of stdout.",
)
def brief(project_name: str, task: str, output_path: Path | None):
    """Generate a task-specific Markdown brief from accepted artifacts."""
    config = load_config()
    db_path = config.db_path
    if not db_path.exists():
        click.echo("No data yet. Run 'aide ingest' first.", err=True)
        raise SystemExit(1)

    from aide.brief import render_project_brief, write_project_brief

    if output_path is None:
        click.echo(render_project_brief(db_path, project_name, task), nl=False)
        return

    write_project_brief(db_path, project_name, task, output_path)
    click.echo(f"Wrote brief to {output_path}")
