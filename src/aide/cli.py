"""CLI entrypoint — aide ingest, aide serve, aide stats."""

from __future__ import annotations

import click

from aide.config import load_config
from aide.db import (
    get_ingested_file,
    get_summary_stats,
    ingest_sessions,
    init_db,
    log_ingestion,
    rebuild_daily_stats,
)
from aide.parser import discover_jsonl_files, parse_jsonl_file


@click.group()
def cli():
    """aide — AI Developer Effectiveness dashboard."""
    pass


@cli.command()
@click.option("--full", is_flag=True, help="Rebuild database from scratch (re-parse all files).")
def ingest(full: bool):
    """Parse Claude Code JSONL logs into SQLite."""
    config = load_config()
    db_path = config.db_path
    log_dir = config.log_dir

    init_db(db_path)

    jsonl_files = discover_jsonl_files(log_dir)
    if not jsonl_files:
        click.echo(f"No JSONL files found in {log_dir}")
        return

    ingested = 0
    skipped = 0

    for file_path in jsonl_files:
        file_key = str(file_path)
        file_stat = file_path.stat()

        # Incremental: skip unchanged files unless --full
        if not full:
            existing = get_ingested_file(db_path, file_key)
            if existing and existing["file_mtime"] == file_stat.st_mtime:
                skipped += 1
                continue

        sessions = parse_jsonl_file(file_path)
        if sessions:
            count = ingest_sessions(db_path, sessions)
            log_ingestion(db_path, file_key, file_stat.st_size, file_stat.st_mtime, count)
            ingested += count

    rebuild_daily_stats(db_path)

    click.echo(f"Ingested {ingested} sessions from {len(jsonl_files) - skipped} files.")
    if skipped:
        click.echo(f"Skipped {skipped} unchanged files.")


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


@cli.command()
@click.argument("session_id")
def autopsy(session_id: str):
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

    session = get_session(db_path, session_id)
    if session is None:
        click.echo(f"Session '{session_id}' not found.", err=True)
        raise SystemExit(1)

    messages = get_session_messages(db_path, session_id)
    tool_calls = get_session_tool_calls(db_path, session_id)
    tool_usage = get_session_tool_usage(db_path, session_id)
    files_touched = get_session_files_touched(db_path, session_id)

    summary = analyze_summary(session, tool_usage, files_touched)
    cost_analysis = analyze_cost(session, messages, tool_calls)
    context = analyze_context(messages)
    suggestions = analyze_suggestions(
        files_touched,
        cost_analysis.cache_efficiency,
        context.estimated_compaction_count,
        session["tool_call_count"],
    )

    report = render_report(summary, cost_analysis, context, suggestions)
    click.echo(report)
