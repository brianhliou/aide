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
    """aide — AI Developer Effectiveness dashboard."""
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
    """Show launchd status for routine ingest and redacted backups."""
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
