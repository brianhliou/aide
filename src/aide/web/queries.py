"""SQL query functions for the dashboard — all reads, no writes.

Each function takes db_path as first argument, opens a connection,
runs queries, and returns plain dicts/lists. Connections are always
closed in a finally block.
"""

from __future__ import annotations

import ast
import shlex
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from pathlib import Path as _Path

from aide.cost import OPENAI_PROVIDERS, estimate_cost
from aide.db import get_connection

# ---------------------------------------------------------------------------
# Error categorization
# ---------------------------------------------------------------------------

# Categories: Test, Lint, Build, Git, Network, Permission, External Service,
# Edit Mismatch, File Access, Other
_BASH_TEST_KW = ("pytest", "python -m pytest", "jest ", "mocha ", "cargo test", "go test")
_BASH_LINT_KW = ("ruff", "mypy", "flake8", "eslint", "prettier", "black ", "isort")
_BASH_BUILD_KW = ("pip ", "uv pip", "npm ", "yarn ", "cargo build", "make ")
_BASH_NETWORK_KW = ("curl ", "wget ", "dig ", "whois ", "ssh ", "scp ", "rsync ")
_BASH_FILE_KW = ("sed ", "rg ", "grep ", "find ", "ls ", "nl ", "cat ", "head ", "tail ")
_BASH_EXTERNAL_KW = ("railway ", "gh ", "modal ", ".venv/bin/modal", "codex mcp")
_PERMISSION_KW = (
    "permission",
    "operation not permitted",
    "sandbox",
    "require_escalated",
    "launchctl",
    "sudo ",
)


def _categorize_error(
    tool_name: str,
    command: str | None,
    description: str | None = None,
) -> str:
    """Classify a tool error into a human-readable category."""
    if tool_name == "Edit":
        return "Edit Mismatch"
    if tool_name in ("Read", "Write", "Glob", "Grep"):
        return "File Access"
    combined = " ".join(part for part in (command, description) if part).lower()
    if combined and any(kw in combined for kw in _PERMISSION_KW):
        return "Permission"
    if tool_name not in ("Bash", "shell") or not command:
        return "Other"
    cmd = command.lower()
    if any(kw in cmd for kw in _BASH_TEST_KW):
        return "Test"
    if any(kw in cmd for kw in _BASH_LINT_KW):
        return "Lint"
    if any(kw in cmd for kw in _BASH_BUILD_KW):
        return "Build"
    if cmd.startswith("git "):
        return "Git"
    if any(kw in cmd for kw in _BASH_NETWORK_KW):
        return "Network"
    if any(kw in cmd for kw in _BASH_EXTERNAL_KW):
        return "External Service"
    if any(kw in cmd for kw in _BASH_FILE_KW):
        return "File Access"
    return "Other"


# These are expected iteration errors, not mistakes
_ITERATION_CATEGORIES = frozenset({"Test", "Lint", "Build"})
_TURN_WAIT_IDLE_THRESHOLD_MS = 30 * 60 * 1000
_ESCALATION_MARKERS = (
    "require_escalated",
    "sandbox_permissions",
    "prefix_rule",
    "approval",
)
_WEAK_PROJECT_NAMES = frozenset({"projects", "codex"})
_VERIFICATION_FAMILIES = frozenset({
    "pytest",
    "ruff",
    "uv run pytest",
    "uv run ruff",
    "npm run test",
    "npm run lint",
    "npm run build",
    "npm run typecheck",
    "yarn test",
    "yarn lint",
    "pnpm test",
    "pnpm lint",
    "just check",
    "just test",
    "just lint",
    "make check",
    "make test",
    "make lint",
})
_BROAD_SEARCH_FAMILIES = frozenset({"rg", "find", "grep", "fd"})
_MUTATION_FAMILIES = frozenset({
    "perl", "sed", "cp", "mv", "tee", "python", "python3", "node", "ruby",
})
_BROWSER_SYSTEM_FAMILIES = frozenset({
    "launchctl", "ps", "lsof", "open", "osascript", "kill", "pkill",
})
_EXTERNAL_SERVICE_FAMILIES = frozenset({
    "curl", "wget", "gh pr", "gh run", "gh issue", "railway status",
    "railway logs", "railway deploy", "vercel logs", "vercel deploy",
})
_EXPENSIVE_NO_EDIT_COST_USD = 1.00
_LOW_ACTIVE_TIME_SECONDS = 60
_LOW_ACTIVE_TIME_RATIO = 0.05


def get_overview_summary(db_path: Path, provider: str | None = None) -> dict:
    """Summary stats for the overview page.

    Returns:
        {
            last_30d: {sessions, cost, projects},
            this_week: {sessions, cost, projects},
            today: {sessions, cost},
        }
    """
    con = get_connection(db_path)
    try:
        today = date.today()
        thirty_days_ago = (today - timedelta(days=30)).isoformat()
        # Monday of the current week
        week_start = (today - timedelta(days=today.weekday())).isoformat()
        today_str = today.isoformat()

        provider_filter = "AND provider = ?" if provider else ""
        provider_params = (provider,) if provider else ()
        wb_provider_filter = "AND provider = ?" if provider else ""

        row_30d = con.execute(
            f"""SELECT
                COUNT(*) AS sessions,
                COALESCE(SUM(estimated_cost_usd), 0) AS cost,
                COUNT(DISTINCT project_name) AS projects
            FROM sessions
            WHERE date(started_at) >= ? {provider_filter}""",
            (thirty_days_ago, *provider_params),
        ).fetchone()

        row_week = con.execute(
            f"""SELECT
                COUNT(*) AS sessions,
                COALESCE(SUM(estimated_cost_usd), 0) AS cost,
                COUNT(DISTINCT project_name) AS projects
            FROM sessions
            WHERE date(started_at) >= ? {provider_filter}""",
            (week_start, *provider_params),
        ).fetchone()

        row_today = con.execute(
            f"""SELECT
                COUNT(*) AS sessions,
                COALESCE(SUM(estimated_cost_usd), 0) AS cost
            FROM sessions
            WHERE date(started_at) = ? {provider_filter}""",
            (today_str, *provider_params),
        ).fetchone()

        # Work block counts for each period
        wb_30d = con.execute(
            f"""SELECT COUNT(*) AS n FROM work_blocks
            WHERE date(started_at) >= ? {wb_provider_filter}""",
            (thirty_days_ago, *provider_params),
        ).fetchone()
        wb_week = con.execute(
            f"""SELECT COUNT(*) AS n FROM work_blocks
            WHERE date(started_at) >= ? {wb_provider_filter}""",
            (week_start, *provider_params),
        ).fetchone()
        wb_today = con.execute(
            f"""SELECT COUNT(*) AS n FROM work_blocks
            WHERE date(started_at) = ? {wb_provider_filter}""",
            (today_str, *provider_params),
        ).fetchone()

        return {
            "last_30d": {
                "sessions": row_30d["sessions"],
                "work_blocks": wb_30d["n"],
                "cost": row_30d["cost"],
                "projects": row_30d["projects"],
            },
            "this_week": {
                "sessions": row_week["sessions"],
                "work_blocks": wb_week["n"],
                "cost": row_week["cost"],
                "projects": row_week["projects"],
            },
            "today": {
                "sessions": row_today["sessions"],
                "work_blocks": wb_today["n"],
                "cost": row_today["cost"],
            },
        }
    finally:
        con.close()


def get_data_freshness(db_path: Path) -> list[dict]:
    """Provider-level data freshness for operational dashboard checks.

    Returns one row for each known provider without exposing raw source paths.
    """
    providers = ("claude", "codex")
    con = get_connection(db_path)
    try:
        session_rows = con.execute(
            """SELECT
                provider,
                COUNT(*) AS session_count,
                MAX(started_at) AS latest_session_at,
                MAX(ingested_at) AS latest_session_ingested_at
            FROM sessions
            GROUP BY provider"""
        ).fetchall()
        ingest_rows = con.execute(
            """SELECT
                provider,
                COUNT(*) AS file_count,
                MAX(datetime(file_mtime, 'unixepoch')) AS latest_file_mtime,
                MAX(ingested_at) AS latest_file_ingested_at
            FROM ingest_log
            GROUP BY provider"""
        ).fetchall()

        sessions_by_provider = {row["provider"]: row for row in session_rows}
        ingest_by_provider = {row["provider"]: row for row in ingest_rows}

        result = []
        for item in providers:
            session = sessions_by_provider.get(item)
            ingest = ingest_by_provider.get(item)
            result.append({
                "provider": item,
                "session_count": session["session_count"] if session else 0,
                "latest_session_at": session["latest_session_at"] if session else None,
                "latest_session_ingested_at": (
                    session["latest_session_ingested_at"] if session else None
                ),
                "ingested_file_count": ingest["file_count"] if ingest else 0,
                "latest_file_mtime": ingest["latest_file_mtime"] if ingest else None,
                "latest_file_ingested_at": (
                    ingest["latest_file_ingested_at"] if ingest else None
                ),
            })
        return result
    finally:
        con.close()


def get_daily_cost_series(
    db_path: Path,
    days: int = 90,
    provider: str | None = None,
) -> list[dict]:
    """Daily cost with 7-day moving average.

    Returns:
        [{date, cost, cost_7d_avg}, ...]
    """
    con = get_connection(db_path)
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        provider_filter = "AND provider = ?" if provider else ""
        params = (cutoff, provider) if provider else (cutoff,)
        rows = con.execute(
            f"""SELECT date(started_at) AS date,
                      COALESCE(SUM(estimated_cost_usd), 0) AS cost
            FROM sessions
            WHERE date(started_at) >= ? {provider_filter}
            GROUP BY date(started_at)
            ORDER BY date""",
            params,
        ).fetchall()

        # Build a date→cost map, then fill gaps with $0
        cost_map: dict[str, float] = {}
        for row in rows:
            cost_map[row["date"]] = row["cost"] or 0.0

        if not cost_map:
            return []

        # Generate continuous date range
        start = date.fromisoformat(min(cost_map))
        end = date.fromisoformat(max(cost_map))
        result = []
        costs = []
        current = start
        while current <= end:
            day_str = current.isoformat()
            cost = cost_map.get(day_str, 0.0)
            costs.append(cost)
            window = costs[-7:]
            avg = sum(window) / len(window)
            result.append({
                "date": day_str,
                "cost": round(cost, 4),
                "cost_7d_avg": round(avg, 4),
            })
            current += timedelta(days=1)

        return result
    finally:
        con.close()


def get_weekly_session_counts(
    db_path: Path,
    weeks: int = 12,
    provider: str | None = None,
) -> list[dict]:
    """Session counts grouped by ISO week.

    Returns:
        [{week_start, session_count}, ...]
    """
    con = get_connection(db_path)
    try:
        cutoff = (date.today() - timedelta(weeks=weeks)).isoformat()
        provider_filter = "AND provider = ?" if provider else ""
        params = (cutoff, provider) if provider else (cutoff,)
        rows = con.execute(
            f"""SELECT
                -- SQLite: date(started_at, 'weekday 0', '-6 days') gives Monday
                date(started_at, 'weekday 1', '-7 days') AS week_start,
                COUNT(*) AS session_count
            FROM sessions
            WHERE date(started_at) >= ? {provider_filter}
            GROUP BY week_start
            ORDER BY week_start""",
            params,
        ).fetchall()

        return [{"week_start": r["week_start"], "session_count": r["session_count"]} for r in rows]
    finally:
        con.close()


def get_weekly_work_block_counts(
    db_path: Path,
    weeks: int = 12,
    provider: str | None = None,
) -> list[dict]:
    """Work block counts grouped by ISO week.

    Returns:
        [{week_start, work_block_count}, ...]
    """
    con = get_connection(db_path)
    try:
        cutoff = (date.today() - timedelta(weeks=weeks)).isoformat()
        provider_filter = "AND provider = ?" if provider else ""
        params = (cutoff, provider) if provider else (cutoff,)
        rows = con.execute(
            f"""SELECT
                date(started_at, 'weekday 1', '-7 days') AS week_start,
                COUNT(*) AS work_block_count
            FROM work_blocks
            WHERE date(started_at) >= ? {provider_filter}
            GROUP BY week_start
            ORDER BY week_start""",
            params,
        ).fetchall()

        return [
            {"week_start": r["week_start"], "work_block_count": r["work_block_count"]}
            for r in rows
        ]
    finally:
        con.close()


def get_cost_by_project(db_path: Path, provider: str | None = None) -> list[dict]:
    """Total cost per project, sorted descending.

    Returns:
        [{project_name, total_cost}, ...]
    """
    con = get_connection(db_path)
    try:
        where = "WHERE provider = ?" if provider else ""
        params = (provider,) if provider else ()
        rows = con.execute(
            f"""SELECT
                project_name,
                COALESCE(SUM(estimated_cost_usd), 0) AS total_cost
            FROM sessions
            {where}
            GROUP BY project_name
            ORDER BY total_cost DESC""",
            params,
        ).fetchall()

        return [{"project_name": r["project_name"], "total_cost": r["total_cost"]} for r in rows]
    finally:
        con.close()


def get_token_breakdown(db_path: Path, provider: str | None = None) -> dict:
    """Total token counts across all sessions.

    Returns:
        {input, output, cache_read, cache_creation}
    """
    con = get_connection(db_path)
    try:
        where = "WHERE provider = ?" if provider else ""
        params = (provider,) if provider else ()
        row = con.execute(
            f"""SELECT
                COALESCE(SUM(total_input_tokens), 0) AS input,
                COALESCE(SUM(total_output_tokens), 0) AS output,
                COALESCE(SUM(total_cache_read_tokens), 0) AS cache_read,
                COALESCE(SUM(total_cache_creation_tokens), 0) AS cache_creation
            FROM sessions
            {where}""",
            params,
        ).fetchone()

        return {
            "input": row["input"],
            "output": row["output"],
            "cache_read": row["cache_read"],
            "cache_creation": row["cache_creation"],
        }
    finally:
        con.close()


def get_projects_table(db_path: Path, provider: str | None = None) -> list[dict]:
    """Project summary table for the projects page.

    Returns:
        [{project_name, session_count, total_cost, avg_cost_per_session,
          total_duration_seconds, total_tokens}, ...]
    """
    con = get_connection(db_path)
    try:
        where = "WHERE provider = ?" if provider else ""
        params = (provider,) if provider else ()
        rows = con.execute(
            f"""SELECT
                project_name,
                COUNT(*) AS session_count,
                COALESCE(SUM(estimated_cost_usd), 0) AS total_cost,
                COALESCE(AVG(estimated_cost_usd), 0) AS avg_cost_per_session,
                COALESCE(SUM(CASE
                    WHEN active_duration_seconds > 0 THEN active_duration_seconds
                    ELSE 0
                END), 0) AS total_duration_seconds,
                COALESCE(AVG(CASE
                    WHEN active_duration_seconds > 0 THEN active_duration_seconds
                    ELSE NULL
                END), 0) AS avg_active_duration_seconds,
                COALESCE(SUM(total_input_tokens) + SUM(total_output_tokens)
                    + SUM(total_cache_read_tokens) + SUM(total_cache_creation_tokens), 0)
                    AS total_tokens
            FROM sessions
            {where}
            GROUP BY project_name
            ORDER BY total_cost DESC""",
            params,
        ).fetchall()

        return [
            {
                "project_name": r["project_name"],
                "session_count": r["session_count"],
                "total_cost": r["total_cost"],
                "avg_cost_per_session": r["avg_cost_per_session"],
                "total_duration_seconds": r["total_duration_seconds"],
                "avg_active_duration_seconds": r["avg_active_duration_seconds"],
                "total_tokens": r["total_tokens"],
            }
            for r in rows
        ]
    finally:
        con.close()


def get_session_scatter_data(
    db_path: Path,
    provider: str | None = None,
) -> list[dict]:
    """Scatter plot data: each session as a point.

    Returns:
        [{provider, session_id, project_name, estimated_cost_usd, started_at}, ...]
    """
    con = get_connection(db_path)
    try:
        where = "WHERE provider = ?" if provider else ""
        params = (provider,) if provider else ()
        rows = con.execute(
            f"""SELECT provider, session_id, project_name, estimated_cost_usd, started_at
            FROM sessions
            {where}
            ORDER BY started_at""",
            params,
        ).fetchall()

        return [
            {
                "provider": r["provider"],
                "session_id": r["session_id"],
                "project_name": r["project_name"],
                "estimated_cost_usd": r["estimated_cost_usd"],
                "started_at": r["started_at"],
            }
            for r in rows
        ]
    finally:
        con.close()


def get_sessions_list(
    db_path: Path,
    project_name: str | None = None,
    provider: str | None = None,
    investigation_signal: str | None = None,
    investigation_hours: int = 30 * 24,
) -> list[dict]:
    """Session list for the sessions page.

    Args:
        db_path: Path to SQLite database.
        project_name: Optional filter by project name.
        provider: Optional filter by provider.
        investigation_signal: Optional investigation action slug.
        investigation_hours: Lookback window when investigation_signal is set.

    Returns:
        [{session_id, project_name, started_at, duration_seconds,
          message_count, tool_call_count, estimated_cost_usd}, ...]
    """
    con = get_connection(db_path)
    try:
        signal_keys = None
        if investigation_signal:
            signal_rows = get_investigation_sessions_for_signal(
                db_path,
                investigation_signal,
                hours=investigation_hours,
                provider=provider,
            )
            signal_keys = {
                (row["provider"], row["session_id"]) for row in signal_rows
            }
            if not signal_keys:
                return []

        query = """SELECT
                s.provider, s.session_id, s.project_name, s.started_at, s.duration_seconds,
                s.active_duration_seconds,
                s.message_count, s.user_message_count, s.tool_call_count,
                s.estimated_cost_usd, s.total_input_tokens, s.total_output_tokens,
                s.total_cache_read_tokens, s.total_cache_creation_tokens,
                s.file_edit_count, s.file_write_count, s.compaction_count,
                s.custom_title, s.tool_error_count,
                (SELECT COUNT(*) FROM work_blocks wb
                 WHERE wb.provider = s.provider
                   AND wb.session_id = s.session_id) AS work_block_count
            FROM sessions s"""
        filters = []
        params = []

        if project_name:
            filters.append("s.project_name = ?")
            params.append(project_name)
        if provider:
            filters.append("s.provider = ?")
            params.append(provider)
        if signal_keys is not None:
            signal_clauses = []
            for signal_provider, signal_session_id in sorted(signal_keys):
                signal_clauses.append("(s.provider = ? AND s.session_id = ?)")
                params.extend([signal_provider, signal_session_id])
            filters.append("(" + " OR ".join(signal_clauses) + ")")

        if filters:
            query += " WHERE " + " AND ".join(filters)

        query += " ORDER BY s.started_at DESC"

        rows = con.execute(query, tuple(params)).fetchall()

        return [
            {
                "session_id": r["session_id"],
                "provider": r["provider"],
                "project_name": r["project_name"],
                "started_at": r["started_at"],
                "duration_seconds": r["duration_seconds"],
                "active_duration_seconds": r["active_duration_seconds"] or 0,
                "work_block_count": r["work_block_count"],
                "message_count": r["message_count"],
                "user_message_count": r["user_message_count"],
                "tool_call_count": r["tool_call_count"],
                "estimated_cost_usd": r["estimated_cost_usd"],
                "total_tokens": (
                    (r["total_input_tokens"] or 0) + (r["total_output_tokens"] or 0)
                    + (r["total_cache_read_tokens"] or 0) + (r["total_cache_creation_tokens"] or 0)
                ),
                "edits": (r["file_edit_count"] or 0) + (r["file_write_count"] or 0),
                "had_compaction": (r["compaction_count"] or 0) > 0,
                "custom_title": r["custom_title"],
                "tool_error_count": r["tool_error_count"] or 0,
            }
            for r in rows
        ]
    finally:
        con.close()


def get_investigation_sessions_for_signal(
    db_path: Path,
    signal: str,
    hours: int = 30 * 24,
    provider: str | None = None,
) -> list[dict]:
    """Return review-queue sessions matching a normalized action signal."""
    rows = get_investigation_queue(
        db_path,
        hours=hours,
        provider=provider,
        limit=100000,
    )
    return [
        row for row in rows
        if _investigation_row_matches_signal(row, signal)
    ]


def get_investigation_signal_label(signal: str | None) -> str | None:
    """Human-readable label for a signal slug."""
    if not signal:
        return None
    for label in _investigation_action_labels():
        if _investigation_label_slug(label) == signal:
            return label
    return signal.replace("-", " ")


def get_session_detail(
    db_path: Path,
    session_id: str,
    provider: str | None = None,
) -> dict | None:
    """Full detail for a single session.

    Returns:
        Dict with all session fields plus:
        - tool_usage: [{tool_name, count}, ...]
        - files_touched: [{file_path, read_count, edit_count, write_count, total}, ...]
        Returns None if session not found.
    """
    con = get_connection(db_path)
    try:
        if provider is None:
            session = con.execute(
                """SELECT * FROM sessions
                WHERE session_id = ?
                ORDER BY CASE WHEN provider = 'claude' THEN 0 ELSE 1 END
                LIMIT 1""",
                (session_id,),
            ).fetchone()
        else:
            session = con.execute(
                "SELECT * FROM sessions WHERE provider = ? AND session_id = ?",
                (provider, session_id),
            ).fetchone()

        if session is None:
            return None

        detail = dict(session)
        provider = detail["provider"]
        max_turn_duration_ms = detail.get("max_turn_duration_ms") or 0
        detail["turn_metrics_reliable"] = (
            max_turn_duration_ms <= _TURN_WAIT_IDLE_THRESHOLD_MS
        )
        detail["turn_metrics_note"] = (
            "Provider-reported turn waits include an idle gap over 30 minutes, "
            "so turn wait totals are not summarized. Active time excludes this gap."
            if not detail["turn_metrics_reliable"]
            else None
        )

        # Tool usage breakdown
        tool_rows = con.execute(
            """SELECT tool_name, COUNT(*) AS count
            FROM tool_calls
            WHERE provider = ? AND session_id = ?
            GROUP BY tool_name
            ORDER BY count DESC""",
            (provider, session_id),
        ).fetchall()

        detail["tool_usage"] = [
            {"tool_name": r["tool_name"], "count": r["count"]} for r in tool_rows
        ]

        # Files touched with read/edit/write breakdown
        READ_TOOLS = {"Read", "Glob", "Grep"}
        EDIT_TOOLS = {"Edit"}
        WRITE_TOOLS = {"Write"}

        file_rows = con.execute(
            """SELECT tool_name, file_path
            FROM tool_calls
            WHERE provider = ? AND session_id = ? AND file_path IS NOT NULL""",
            (provider, session_id),
        ).fetchall()

        files: dict[str, dict] = {}
        for r in file_rows:
            fp = r["file_path"]
            tn = r["tool_name"]
            if fp not in files:
                files[fp] = {"file_path": fp, "read_count": 0, "edit_count": 0, "write_count": 0}
            if tn in READ_TOOLS:
                files[fp]["read_count"] += 1
            elif tn in EDIT_TOOLS:
                files[fp]["edit_count"] += 1
            elif tn in WRITE_TOOLS:
                files[fp]["write_count"] += 1

        file_list = list(files.values())
        for f in file_list:
            f["total"] = f["read_count"] + f["edit_count"] + f["write_count"]
        file_list.sort(key=lambda x: x["total"], reverse=True)

        detail["files_touched"] = file_list

        # File focus ratio
        focus_row = con.execute(
            """SELECT COUNT(DISTINCT file_path) AS unique_files,
                      COUNT(*) AS total_ops
            FROM tool_calls
            WHERE provider = ? AND session_id = ? AND file_path IS NOT NULL""",
            (provider, session_id),
        ).fetchone()
        uf = focus_row["unique_files"] or 0
        to = focus_row["total_ops"] or 0
        detail["file_focus_ratio"] = uf / to if to > 0 else 0.0
        detail["unique_files"] = uf

        # Thinking ratio for this session
        detail["thinking_ratio"] = (
            detail["thinking_message_count"] / detail["assistant_message_count"]
            if detail.get("assistant_message_count") and detail["assistant_message_count"] > 0
            else 0.0
        )

        # First prompt length
        first_prompt = con.execute(
            """SELECT content_length FROM messages
            WHERE provider = ? AND session_id = ? AND role = 'user'
            ORDER BY id LIMIT 1""",
            (provider, session_id),
        ).fetchone()
        detail["first_prompt_len"] = (
            first_prompt["content_length"] if first_prompt else 0
        )

        # Work blocks for this session
        wb_rows = con.execute(
            """SELECT block_index, started_at, ended_at,
                      duration_seconds, message_count
            FROM work_blocks
            WHERE provider = ? AND session_id = ?
            ORDER BY block_index""",
            (provider, session_id),
        ).fetchall()
        detail["work_blocks"] = [
            {
                "block_index": wb["block_index"],
                "started_at": wb["started_at"],
                "ended_at": wb["ended_at"],
                "duration_seconds": wb["duration_seconds"],
                "message_count": wb["message_count"],
            }
            for wb in wb_rows
        ]

        # Error breakdown for this session
        if detail.get("tool_error_count") and detail["tool_error_count"] > 0:
            err_rows = con.execute(
                """SELECT tool_name, command, description
                FROM tool_calls
                WHERE is_error = 1 AND provider = ? AND session_id = ?""",
                (provider, session_id),
            ).fetchall()
            categories: dict[str, int] = {}
            for r in err_rows:
                cat = _categorize_error(r["tool_name"], r["command"], r["description"])
                categories[cat] = categories.get(cat, 0) + 1
            err_list = [
                {"category": cat, "count": count}
                for cat, count in categories.items()
            ]
            err_list.sort(key=lambda x: x["count"], reverse=True)
            detail["error_breakdown"] = err_list
        else:
            detail["error_breakdown"] = []

        return detail
    finally:
        con.close()


def get_tool_counts(db_path: Path, provider: str | None = None) -> list[dict]:
    """Total usage count per tool, sorted descending.

    Returns:
        [{tool_name, count}, ...]
    """
    con = get_connection(db_path)
    try:
        where = "WHERE provider = ?" if provider else ""
        params = (provider,) if provider else ()
        rows = con.execute(
            f"""SELECT tool_name, COUNT(*) AS count
            FROM tool_calls
            {where}
            GROUP BY tool_name
            ORDER BY count DESC""",
            params,
        ).fetchall()

        return [{"tool_name": r["tool_name"], "count": r["count"]} for r in rows]
    finally:
        con.close()


def get_tool_weekly(
    db_path: Path,
    weeks: int = 12,
    provider: str | None = None,
) -> list[dict]:
    """Tool usage grouped by week and tool name.

    Returns:
        [{week_start, tool_name, count}, ...]
    """
    con = get_connection(db_path)
    try:
        cutoff = (date.today() - timedelta(weeks=weeks)).isoformat()
        provider_filter = "AND provider = ?" if provider else ""
        params = (cutoff, provider) if provider else (cutoff,)
        rows = con.execute(
            f"""SELECT
                date(timestamp, 'weekday 1', '-7 days') AS week_start,
                tool_name,
                COUNT(*) AS count
            FROM tool_calls
            WHERE date(timestamp) >= ? {provider_filter}
            GROUP BY week_start, tool_name
            ORDER BY week_start, count DESC""",
            params,
        ).fetchall()

        return [
            {"week_start": r["week_start"], "tool_name": r["tool_name"], "count": r["count"]}
            for r in rows
        ]
    finally:
        con.close()


def get_tool_daily(
    db_path: Path,
    days: int = 90,
    provider: str | None = None,
) -> list[dict]:
    """Tool usage grouped by day and tool name.

    Returns:
        [{date, tool_name, count}, ...]
    """
    con = get_connection(db_path)
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        provider_filter = "AND provider = ?" if provider else ""
        params = (cutoff, provider) if provider else (cutoff,)
        rows = con.execute(
            f"""SELECT
                date(timestamp) AS date,
                tool_name,
                COUNT(*) AS count
            FROM tool_calls
            WHERE date(timestamp) >= ? {provider_filter}
            GROUP BY date, tool_name
            ORDER BY date, count DESC""",
            params,
        ).fetchall()

        return [
            {"date": r["date"], "tool_name": r["tool_name"], "count": r["count"]}
            for r in rows
        ]
    finally:
        con.close()


def get_effectiveness_summary(db_path: Path, provider: str | None = None) -> dict:
    """Effectiveness metrics aggregated across all sessions.

    Returns 9 metrics derived from exact token/tool counts, plus
    iteration_error_pct (fraction of errors that are test/lint/build):
        {cache_hit_rate, edit_ratio, compaction_rate, read_to_edit_ratio,
         output_ratio, tokens_per_user_msg, turns_per_user_prompt,
         error_rate, iteration_error_pct, rework_rate, session_count}
    """
    con = get_connection(db_path)
    try:
        where = "WHERE provider = ?" if provider else ""
        params = (provider,) if provider else ()
        row = con.execute(
            f"""SELECT
                COUNT(*) AS session_count,
                COALESCE(SUM(total_cache_read_tokens), 0) AS total_cache_read,
                COALESCE(SUM(total_input_tokens), 0) AS total_input,
                COALESCE(SUM(total_cache_creation_tokens), 0) AS total_cache_creation,
                COALESCE(SUM(total_output_tokens), 0) AS total_output,
                COALESCE(SUM(tool_call_count), 0) AS total_tools,
                COALESCE(SUM(file_edit_count), 0) AS total_edits,
                COALESCE(SUM(file_write_count), 0) AS total_writes,
                COALESCE(SUM(file_read_count), 0) AS total_reads,
                COALESCE(SUM(user_message_count), 0) AS total_user_msgs,
                COALESCE(SUM(assistant_message_count), 0) AS total_asst_msgs,
                COALESCE(SUM(CASE WHEN compaction_count > 0 THEN 1 ELSE 0 END), 0)
                    AS sessions_with_compaction,
                COALESCE(SUM(tool_error_count), 0) AS total_errors,
                COALESCE(SUM(CASE WHEN rework_file_count > 0 THEN 1 ELSE 0 END), 0)
                    AS sessions_with_rework,
                COALESCE(SUM(estimated_cost_usd), 0) AS total_cost
            FROM sessions
            {where}""",
            params,
        ).fetchone()

        n = row["session_count"]
        cache_read = row["total_cache_read"]
        if provider and provider.lower() in OPENAI_PROVIDERS:
            input_denom = row["total_input"] + row["total_cache_creation"]
        else:
            input_denom = row["total_input"] + cache_read + row["total_cache_creation"]
        edit_write = row["total_edits"] + row["total_writes"]
        io_total = row["total_input"] + row["total_output"]
        all_tokens = input_denom + row["total_output"]
        user_msgs = row["total_user_msgs"]
        total_errors = row["total_errors"]

        # Categorize errors to compute iteration percentage
        iteration_errors = 0
        if total_errors > 0:
            provider_filter = "AND provider = ?" if provider else ""
            err_params = (provider,) if provider else ()
            err_rows = con.execute(
                f"""SELECT tool_name, command, description
                FROM tool_calls
                WHERE is_error = 1 {provider_filter}""",
                err_params,
            ).fetchall()
            for r in err_rows:
                cat = _categorize_error(r["tool_name"], r["command"], r["description"])
                if cat in _ITERATION_CATEGORIES:
                    iteration_errors += 1

        return {
            "cache_hit_rate": cache_read / input_denom if input_denom > 0 else 0.0,
            "edit_ratio": edit_write / row["total_tools"] if row["total_tools"] > 0 else 0.0,
            "compaction_rate": row["sessions_with_compaction"] / n if n > 0 else 0.0,
            "read_to_edit_ratio": row["total_reads"] / max(edit_write, 1),
            "output_ratio": (
                row["total_output"] / io_total if io_total > 0 else 0.0
            ),
            "tokens_per_user_msg": all_tokens // user_msgs if user_msgs > 0 else 0,
            "turns_per_user_prompt": (
                row["total_asst_msgs"] / user_msgs if user_msgs > 0 else 0.0
            ),
            "error_rate": (
                total_errors / row["total_tools"] if row["total_tools"] > 0 else 0.0
            ),
            "iteration_error_pct": (
                iteration_errors / total_errors if total_errors > 0 else 0.0
            ),
            "rework_rate": row["sessions_with_rework"] / n if n > 0 else 0.0,
            "cost_per_edit": (
                row["total_cost"] / edit_write if edit_write > 0 else 0.0
            ),
            "session_count": n,
        }
    finally:
        con.close()


def get_effectiveness_trends(
    db_path: Path,
    days: int = 90,
    provider: str | None = None,
) -> list[dict]:
    """Per-session effectiveness metrics for trend charts.

    Returns:
        [{date, session_id, cache_hit_rate, edit_ratio, had_compaction}, ...]
        Sorted by started_at ascending.
    """
    con = get_connection(db_path)
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        provider_filter = "AND provider = ?" if provider else ""
        params = (cutoff, provider) if provider else (cutoff,)
        rows = con.execute(
            f"""SELECT
                session_id,
                started_at,
                date(started_at) AS date,
                total_input_tokens,
                total_cache_read_tokens,
                total_cache_creation_tokens,
                tool_call_count,
                file_edit_count,
                file_write_count,
                compaction_count
            FROM sessions
            WHERE date(started_at) >= ? {provider_filter}
            ORDER BY started_at""",
            params,
        ).fetchall()

        result = []
        for r in rows:
            if provider and provider.lower() in OPENAI_PROVIDERS:
                denom = (
                    (r["total_input_tokens"] or 0)
                    + (r["total_cache_creation_tokens"] or 0)
                )
            else:
                denom = (
                    (r["total_input_tokens"] or 0)
                    + (r["total_cache_read_tokens"] or 0)
                    + (r["total_cache_creation_tokens"] or 0)
                )
            cache_hit_rate = (
                (r["total_cache_read_tokens"] or 0) / denom if denom > 0 else 0.0
            )
            tools = r["tool_call_count"] or 0
            edits_writes = (r["file_edit_count"] or 0) + (r["file_write_count"] or 0)
            edit_ratio = edits_writes / tools if tools > 0 else 0.0
            result.append({
                "started_at": r["started_at"],
                "date": r["date"],
                "session_id": r["session_id"],
                "cache_hit_rate": round(cache_hit_rate, 4),
                "edit_ratio": round(edit_ratio, 4),
                "had_compaction": (r["compaction_count"] or 0) > 0,
            })

        return result
    finally:
        con.close()


def get_top_files(
    db_path: Path,
    limit: int = 20,
    provider: str | None = None,
) -> list[dict]:
    """Most-accessed files across all sessions.

    Categorizes tools: Read/Glob/Grep = read, Edit = edit, Write = write.
    Only includes tool_calls where file_path is not NULL.

    Returns:
        [{file_path, read_count, edit_count, write_count, total}, ...]
    """
    READ_TOOLS = {"Read", "Glob", "Grep"}
    EDIT_TOOLS = {"Edit"}
    WRITE_TOOLS = {"Write"}

    con = get_connection(db_path)
    try:
        provider_filter = "AND provider = ?" if provider else ""
        params = (provider,) if provider else ()
        rows = con.execute(
            f"""SELECT tool_name, file_path
            FROM tool_calls
            WHERE file_path IS NOT NULL
                AND tool_name IN ('Read', 'Glob', 'Grep', 'Edit', 'Write')
                {provider_filter}""",
            params,
        ).fetchall()

        files: dict[str, dict] = {}
        for r in rows:
            fp = r["file_path"]
            tn = r["tool_name"]
            if fp not in files:
                files[fp] = {"file_path": fp, "read_count": 0, "edit_count": 0, "write_count": 0}
            if tn in READ_TOOLS:
                files[fp]["read_count"] += 1
            elif tn in EDIT_TOOLS:
                files[fp]["edit_count"] += 1
            elif tn in WRITE_TOOLS:
                files[fp]["write_count"] += 1

        file_list = list(files.values())
        for f in file_list:
            f["total"] = f["read_count"] + f["edit_count"] + f["write_count"]
        file_list.sort(key=lambda x: x["total"], reverse=True)

        return file_list[:limit]
    finally:
        con.close()


def get_top_bash_commands(
    db_path: Path,
    limit: int = 20,
    provider: str | None = None,
) -> list[dict]:
    """Most common Bash commands with error rates.

    Returns:
        [{command, count, error_count, error_rate}, ...]
    """
    con = get_connection(db_path)
    try:
        provider_filter = "AND provider = ?" if provider else ""
        params = (provider, limit) if provider else (limit,)
        rows = con.execute(
            f"""SELECT
                command,
                COUNT(*) AS count,
                SUM(is_error) AS error_count
            FROM tool_calls
            WHERE tool_name = 'Bash' AND command IS NOT NULL {provider_filter}
            GROUP BY command
            ORDER BY count DESC
            LIMIT ?""",
            params,
        ).fetchall()

        return [
            {
                "command": r["command"],
                "count": r["count"],
                "error_count": r["error_count"] or 0,
                "error_rate": (
                    (r["error_count"] or 0) / r["count"]
                    if r["count"] > 0 else 0.0
                ),
            }
            for r in rows
        ]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Insights queries
# ---------------------------------------------------------------------------


def get_first_prompt_analysis(db_path: Path, provider: str | None = None) -> dict:
    """Analyze correlation between first prompt length and session outcomes.

    Returns:
        {buckets: [{label, n, avg_cost, avg_errors, avg_edits}, ...],
         scatter: [{prompt_len, cost, errors}, ...]}
    """
    con = get_connection(db_path)
    try:
        provider_filter = "AND s.provider = ?" if provider else ""
        params = (provider,) if provider else ()
        rows = con.execute(
            f"""SELECT s.provider, s.session_id, s.estimated_cost_usd, s.tool_error_count,
                      s.file_edit_count + s.file_write_count AS edits,
                      m.content_length AS first_prompt_len
            FROM sessions s
            JOIN messages m
              ON m.provider = s.provider
             AND m.session_id = s.session_id
            WHERE m.role = 'user'
              AND m.id = (
                  SELECT MIN(id) FROM messages
                  WHERE provider = s.provider
                    AND session_id = s.session_id
                    AND role = 'user'
              )
              AND m.content_length > 0
              {provider_filter}""",
            params,
        ).fetchall()

        if not rows:
            return {"buckets": [], "scatter": []}

        # Bucket definitions
        buckets_def = [
            ("< 100 chars", 0, 100),
            ("100–300", 100, 300),
            ("300–700", 300, 700),
            ("700+", 700, 999999),
        ]
        bucket_data: dict[str, list] = {label: [] for label, _, _ in buckets_def}

        scatter = []
        for r in rows:
            pl = r["first_prompt_len"] or 0
            scatter.append({
                "prompt_len": pl,
                "cost": r["estimated_cost_usd"] or 0,
                "errors": r["tool_error_count"] or 0,
            })
            for label, lo, hi in buckets_def:
                if lo <= pl < hi:
                    bucket_data[label].append(r)
                    break

        buckets = []
        for label, _, _ in buckets_def:
            items = bucket_data[label]
            if not items:
                continue
            n = len(items)
            buckets.append({
                "label": label,
                "n": n,
                "avg_cost": sum(r["estimated_cost_usd"] or 0 for r in items) / n,
                "avg_errors": sum(r["tool_error_count"] or 0 for r in items) / n,
                "avg_edits": sum(r["edits"] or 0 for r in items) / n,
            })

        return {"buckets": buckets, "scatter": scatter}
    finally:
        con.close()


def get_cost_concentration(db_path: Path, provider: str | None = None) -> dict:
    """Cost distribution across sessions (Pareto analysis).

    Returns:
        {sessions: [{session_id, project_name, cost, cumulative_pct,
                     custom_title, started_at}, ...],
         top3_pct: float, median: float, p90: float}
    """
    con = get_connection(db_path)
    try:
        provider_filter = "AND provider = ?" if provider else ""
        params = (provider,) if provider else ()
        rows = con.execute(
            f"""SELECT provider, session_id, project_name, estimated_cost_usd,
                      custom_title, started_at
            FROM sessions
            WHERE estimated_cost_usd > 0 {provider_filter}
            ORDER BY estimated_cost_usd DESC""",
            params,
        ).fetchall()

        if not rows:
            return {"sessions": [], "top3_pct": 0, "median": 0, "p90": 0}

        total_cost = sum(r["estimated_cost_usd"] for r in rows)
        cumulative = 0.0
        sessions = []
        for r in rows:
            cumulative += r["estimated_cost_usd"]
            sessions.append({
                "provider": r["provider"],
                "session_id": r["session_id"],
                "project_name": r["project_name"],
                "cost": r["estimated_cost_usd"],
                "cumulative_pct": cumulative / total_cost if total_cost > 0 else 0,
                "custom_title": r["custom_title"],
                "started_at": r["started_at"],
            })

        costs_asc = sorted(r["estimated_cost_usd"] for r in rows)
        n = len(costs_asc)
        top3_cost = sum(s["cost"] for s in sessions[:3])

        return {
            "sessions": sessions[:10],  # top 10 for the table
            "top3_pct": top3_cost / total_cost if total_cost > 0 else 0,
            "median": costs_asc[n // 2],
            "p90": costs_asc[int(n * 0.9)] if n > 1 else costs_asc[0],
            "total_cost": total_cost,
        }
    finally:
        con.close()


def get_cost_per_edit_by_duration(
    db_path: Path,
    provider: str | None = None,
) -> list[dict]:
    """Cost per edit grouped by session duration bucket.

    Returns:
        [{label, n, avg_cost_per_edit, total_cost, total_edits}, ...]
    """
    con = get_connection(db_path)
    try:
        provider_filter = "AND provider = ?" if provider else ""
        params = (provider,) if provider else ()
        rows = con.execute(
            f"""SELECT active_duration_seconds AS duration_seconds,
                      estimated_cost_usd,
                      file_edit_count + file_write_count AS edits
            FROM sessions
            WHERE (file_edit_count + file_write_count) > 0
              AND estimated_cost_usd > 0
              {provider_filter}""",
            params,
        ).fetchall()

        buckets_def = [
            ("< 30 min", 0, 1800),
            ("30m – 2h", 1800, 7200),
            ("> 2 hours", 7200, 999999),
        ]

        result = []
        for label, lo, hi in buckets_def:
            items = [r for r in rows if lo <= (r["duration_seconds"] or 0) < hi]
            if not items:
                continue
            total_cost = sum(r["estimated_cost_usd"] for r in items)
            total_edits = sum(r["edits"] for r in items)
            result.append({
                "label": label,
                "n": len(items),
                "avg_cost_per_edit": total_cost / total_edits if total_edits else 0,
                "total_cost": total_cost,
                "total_edits": total_edits,
            })

        return result
    finally:
        con.close()


def get_model_breakdown(db_path: Path, provider: str | None = None) -> list[dict]:
    """Token usage and estimated cost per model.

    Returns:
        [{provider, model, msg_count, input_tokens, output_tokens, cache_tokens,
          estimated_cost}, ...]
    """
    con = get_connection(db_path)
    try:
        provider_filter = "AND provider = ?" if provider else ""
        params = (provider,) if provider else ()
        rows = con.execute(
            f"""SELECT provider,
                      model,
                      COUNT(*) AS msg_count,
                      COALESCE(SUM(input_tokens), 0) AS input_tokens,
                      COALESCE(SUM(output_tokens), 0) AS output_tokens,
                      COALESCE(SUM(cache_read_tokens), 0) AS cache_tokens
            FROM messages
            WHERE model IS NOT NULL AND model != '<synthetic>' {provider_filter}
            GROUP BY provider, model
            ORDER BY SUM(output_tokens) DESC""",
            params,
        ).fetchall()

        result = []
        for r in rows:
            provider = r["provider"] or "claude"
            cost = estimate_cost(
                r["input_tokens"],
                r["output_tokens"],
                cache_read_tokens=r["cache_tokens"],
                model=r["model"],
                provider=provider,
            )
            result.append({
                "provider": provider,
                "model": r["model"],
                "model_short": r["model"].split("-202")[0] if "-202" in r["model"] else r["model"],
                "msg_count": r["msg_count"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "cache_tokens": r["cache_tokens"],
                "estimated_cost": round(cost, 2),
            })

        return result
    finally:
        con.close()


def get_tool_sequences(
    db_path: Path,
    limit: int = 15,
    provider: str | None = None,
) -> list[dict]:
    """Most common tool→tool transitions.

    Returns:
        [{from_tool, to_tool, count}, ...]
    """
    con = get_connection(db_path)
    try:
        where = "WHERE provider = ?" if provider else ""
        params = (provider,) if provider else ()
        rows = con.execute(
            f"""SELECT provider, session_id, tool_name
            FROM tool_calls
            {where}
            ORDER BY provider, session_id, timestamp, id""",
            params,
        ).fetchall()

        from collections import Counter
        transitions: Counter[tuple[str, str]] = Counter()
        prev_tool = None
        prev_key = None
        for r in rows:
            key = (r["provider"], r["session_id"])
            if key == prev_key and prev_tool:
                transitions[(prev_tool, r["tool_name"])] += 1
            prev_tool = r["tool_name"]
            prev_key = key

        return [
            {"from_tool": a, "to_tool": b, "count": c}
            for (a, b), c in transitions.most_common(limit)
        ]
    finally:
        con.close()


def get_time_patterns(db_path: Path, provider: str | None = None) -> dict:
    """Work block start patterns by hour-of-day and day-of-week.

    Uses work_blocks table so patterns reflect actual sit-down coding time,
    not misleading session start times for multi-day sessions.

    Returns:
        {by_hour: [{hour, count}, ...],
         by_day: [{day, day_name, count}, ...]}
    """
    con = get_connection(db_path)
    try:
        where = "WHERE provider = ?" if provider else ""
        params = (provider,) if provider else ()
        hour_rows = con.execute(
            f"""SELECT
                CAST(substr(started_at, 12, 2) AS INTEGER) AS hour,
                COUNT(*) AS count
            FROM work_blocks
            {where}
            GROUP BY hour
            ORDER BY hour""",
            params,
        ).fetchall()

        day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        day_rows = con.execute(
            f"""SELECT
                CAST(strftime('%w', started_at) AS INTEGER) AS day_num,
                COUNT(*) AS count
            FROM work_blocks
            {where}
            GROUP BY day_num
            ORDER BY day_num""",
            params,
        ).fetchall()

        return {
            "by_hour": [
                {"hour": r["hour"], "count": r["count"]}
                for r in hour_rows
            ],
            "by_day": [
                {"day": r["day_num"], "day_name": day_names[r["day_num"]],
                 "count": r["count"]}
                for r in day_rows
            ],
        }
    finally:
        con.close()


def get_user_response_times(db_path: Path, provider: str | None = None) -> dict:
    """Distribution of user response times (gap between assistant and next user msg).

    Returns:
        {median_seconds: float, mean_seconds: float, count: int,
         buckets: [{label, count, pct}, ...]}
    """
    con = get_connection(db_path)
    try:
        provider_filter = "AND provider = ?" if provider else ""
        params = (provider,) if provider else ()
        rows = con.execute(
            f"""SELECT provider, session_id, role, timestamp
            FROM messages
            WHERE role IN ('user', 'assistant') {provider_filter}
            ORDER BY provider, session_id, timestamp""",
            params,
        ).fetchall()

        from datetime import datetime as dt
        gaps = []
        prev_role = None
        prev_ts = None
        prev_key = None
        for r in rows:
            key = (r["provider"], r["session_id"])
            if (key == prev_key
                    and r["role"] == "user"
                    and prev_role == "assistant"
                    and prev_ts):
                try:
                    t1 = dt.fromisoformat(prev_ts.replace("Z", "+00:00"))
                    t2 = dt.fromisoformat(
                        r["timestamp"].replace("Z", "+00:00")
                    )
                    gap = (t2 - t1).total_seconds()
                    if 1 < gap < 3600:  # skip sub-1s (tool results) and >1hr
                        gaps.append(gap)
                except (ValueError, TypeError):
                    pass
            prev_role = r["role"]
            prev_ts = r["timestamp"]
            prev_key = key

        if not gaps:
            return {
                "median_seconds": 0, "mean_seconds": 0,
                "count": 0, "buckets": [],
            }

        gaps.sort()
        n = len(gaps)
        bucket_defs = [
            ("< 10s", 0, 10),
            ("10–30s", 10, 30),
            ("30s–2m", 30, 120),
            ("2–5m", 120, 300),
            ("5–15m", 300, 900),
            ("15m+", 900, 3600),
        ]
        buckets = []
        for label, lo, hi in bucket_defs:
            count = sum(1 for g in gaps if lo <= g < hi)
            if count > 0:
                buckets.append({
                    "label": label,
                    "count": count,
                    "pct": count / n,
                })

        return {
            "median_seconds": gaps[n // 2],
            "mean_seconds": sum(gaps) / n,
            "count": n,
            "buckets": buckets,
        }
    finally:
        con.close()


def get_thinking_stats(db_path: Path, provider: str | None = None) -> dict:
    """Thinking block analysis across sessions.

    Returns:
        {sessions_with_thinking: int, total_sessions: int,
         avg_thinking_chars: float,
         by_session: [{session_id, project_name, custom_title, started_at,
                       thinking_chars, thinking_messages, estimated_cost_usd,
                       tool_error_count}, ...]}
    """
    con = get_connection(db_path)
    try:
        provider_filter = "AND provider = ?" if provider else ""
        params = (provider,) if provider else ()
        rows = con.execute(
            f"""SELECT provider, session_id, project_name, custom_title, started_at,
                      total_thinking_chars, thinking_message_count,
                      estimated_cost_usd, tool_error_count
            FROM sessions
            WHERE total_thinking_chars > 0 {provider_filter}
            ORDER BY total_thinking_chars DESC""",
            params,
        ).fetchall()

        where = "WHERE provider = ?" if provider else ""
        total_sessions = con.execute(
            f"SELECT COUNT(*) AS n FROM sessions {where}",
            params,
        ).fetchone()["n"]

        total_chars = sum(r["total_thinking_chars"] for r in rows)
        n = len(rows)

        return {
            "sessions_with_thinking": n,
            "total_sessions": total_sessions,
            "avg_thinking_chars": total_chars / n if n > 0 else 0,
            "by_session": [
                {
                    "provider": r["provider"],
                    "session_id": r["session_id"],
                    "project_name": r["project_name"],
                    "custom_title": r["custom_title"],
                    "started_at": r["started_at"],
                    "thinking_chars": r["total_thinking_chars"],
                    "thinking_messages": r["thinking_message_count"],
                    "estimated_cost_usd": r["estimated_cost_usd"],
                    "tool_error_count": r["tool_error_count"] or 0,
                }
                for r in rows[:10]
            ],
        }
    finally:
        con.close()


def get_permission_mode_breakdown(
    db_path: Path,
    provider: str | None = None,
) -> list[dict]:
    """Permission mode distribution across sessions.

    Returns:
        [{mode, count, pct}, ...] sorted by count desc.
    """
    con = get_connection(db_path)
    try:
        provider_filter = "AND provider = ?" if provider else ""
        params = (provider,) if provider else ()
        rows = con.execute(
            f"""SELECT permission_mode, COUNT(*) AS count
            FROM sessions
            WHERE permission_mode IS NOT NULL {provider_filter}
            GROUP BY permission_mode
            ORDER BY count DESC""",
            params,
        ).fetchall()

        total = sum(r["count"] for r in rows)
        return [
            {
                "mode": r["permission_mode"],
                "count": r["count"],
                "pct": r["count"] / total if total > 0 else 0,
            }
            for r in rows
        ]
    finally:
        con.close()


def get_permission_friction_summary(
    db_path: Path,
    hours: int = 36,
    provider: str | None = None,
) -> dict:
    """Recent permission and sandbox friction from normalized tool-call rows.

    Counts explicit escalation/approval markers and permission-categorized tool
    errors without returning raw command strings or tool output.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    con = get_connection(db_path)
    try:
        provider_filter = "AND s.provider = ?" if provider else ""
        params = (cutoff.isoformat(), provider) if provider else (cutoff.isoformat(),)

        sessions = con.execute(
            f"""SELECT s.provider, s.session_id, s.project_name, s.started_at,
                      s.tool_call_count, s.tool_error_count
            FROM sessions s
            WHERE s.started_at >= ? {provider_filter}""",
            params,
        ).fetchall()

        calls = con.execute(
            f"""SELECT tc.provider, tc.session_id, s.project_name, s.started_at,
                      s.tool_call_count, s.tool_error_count,
                      tc.tool_name, tc.command, tc.description, tc.is_error
            FROM tool_calls tc
            JOIN sessions s
              ON s.provider = tc.provider
             AND s.session_id = tc.session_id
            WHERE s.started_at >= ? {provider_filter}
            ORDER BY s.started_at DESC, tc.timestamp, tc.id""",
            params,
        ).fetchall()

        session_meta = {
            (row["provider"], row["session_id"]): row for row in sessions
        }
        session_counts: dict[tuple[str, str], dict] = {
            key: {
                "provider": row["provider"],
                "session_id": row["session_id"],
                "project_name": row["project_name"],
                "started_at": row["started_at"],
                "tool_call_count": row["tool_call_count"] or 0,
                "tool_error_count": row["tool_error_count"] or 0,
                "escalation_markers": 0,
                "permission_errors": 0,
                "unique_friction_calls": 0,
            }
            for key, row in session_meta.items()
        }

        provider_counts: dict[str, int] = {}
        project_counts: dict[str, int] = {}
        command_family_counts: dict[str, int] = {}
        permission_error_family_counts: dict[str, int] = {}
        cause_counts: dict[str, dict] = {}
        allowlist_candidate_counts: dict[str, dict] = {}
        keep_on_request_counts: dict[str, dict] = {}
        escalation_count = 0
        permission_error_count = 0
        unique_friction_count = 0
        no_prefix_rule_escalations = 0
        sessions_with_friction: set[tuple[str, str]] = set()

        for row in calls:
            is_escalation = _has_escalation_marker(
                row["command"], row["description"],
            )
            is_permission_error = bool(row["is_error"]) and _categorize_error(
                row["tool_name"], row["command"], row["description"],
            ) == "Permission"
            if not is_escalation and not is_permission_error:
                continue

            key = (row["provider"], row["session_id"])
            sessions_with_friction.add(key)
            unique_friction_count += 1
            provider_counts[row["provider"]] = provider_counts.get(row["provider"], 0) + 1
            project_counts[row["project_name"]] = project_counts.get(row["project_name"], 0) + 1

            family = _command_family(row["command"])
            command_family_counts[family] = command_family_counts.get(family, 0) + 1
            for cause in _permission_friction_causes(
                row["command"],
                row["description"],
                family=family,
                is_escalation=is_escalation,
            ):
                _increment_cause_bucket(cause_counts, cause)
            if key in session_counts:
                session_counts[key]["unique_friction_calls"] += 1

            if is_escalation:
                escalation_count += 1
                if key in session_counts:
                    session_counts[key]["escalation_markers"] += 1
                if _extract_prefix_rule(row["description"]) is None:
                    no_prefix_rule_escalations += 1
                    candidate = _allowlist_candidate(row["command"])
                    if candidate is None:
                        _increment_policy_bucket(
                            keep_on_request_counts,
                            family,
                            "Keep this command family on-request; it can scan broadly, "
                            "mutate files, run arbitrary code, or touch external/system resources.",
                        )
                    else:
                        _increment_policy_bucket(
                            allowlist_candidate_counts,
                            candidate["prefix_rule"],
                            candidate["reason"],
                        )

            if is_permission_error:
                permission_error_count += 1
                permission_error_family_counts[family] = (
                    permission_error_family_counts.get(family, 0) + 1
                )
                if key in session_counts:
                    session_counts[key]["permission_errors"] += 1

        top_sessions = [
            value for value in session_counts.values()
            if value["unique_friction_calls"] > 0
        ]
        top_sessions.sort(
            key=lambda item: (
                item["unique_friction_calls"],
                item["permission_errors"],
                item["tool_error_count"],
            ),
            reverse=True,
        )

        total_tool_calls = sum(row["tool_call_count"] or 0 for row in sessions)
        session_total = len(sessions)
        return {
            "window_hours": hours,
            "session_count": session_total,
            "tool_call_count": total_tool_calls,
            "unique_friction_calls": unique_friction_count,
            "escalation_markers": escalation_count,
            "permission_errors": permission_error_count,
            "sessions_with_friction": len(sessions_with_friction),
            "friction_rate_per_session": (
                unique_friction_count / session_total if session_total > 0 else 0.0
            ),
            "friction_rate_per_100_tool_calls": (
                unique_friction_count / total_tool_calls * 100
                if total_tool_calls > 0 else 0.0
            ),
            "no_prefix_rule_escalations": no_prefix_rule_escalations,
            "cause_breakdown": _policy_rows(cause_counts)[:8],
            "allowlist_candidates": _policy_rows(allowlist_candidate_counts)[:6],
            "keep_on_request": _policy_rows(keep_on_request_counts)[:6],
            "provider_breakdown": _counter_rows(provider_counts, "provider"),
            "project_breakdown": _counter_rows(project_counts, "project_name")[:8],
            "command_families": _counter_rows(command_family_counts, "family")[:10],
            "permission_error_families": _counter_rows(
                permission_error_family_counts, "family",
            )[:10],
            "top_sessions": top_sessions[:8],
        }
    finally:
        con.close()


def get_investigation_queue(
    db_path: Path,
    hours: int = 36,
    provider: str | None = None,
    limit: int = 12,
) -> list[dict]:
    """Rank recent sessions that most deserve manual review.

    This intentionally uses only normalized aggregate signals and error
    categories. It does not expose raw commands, prompts, or tool output.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    con = get_connection(db_path)
    try:
        provider_filter = "AND provider = ?" if provider else ""
        session_params = (
            (cutoff.isoformat(), provider) if provider else (cutoff.isoformat(),)
        )
        sessions = con.execute(
            f"""SELECT provider, session_id, project_path, project_name,
                      custom_title, started_at,
                      estimated_cost_usd, tool_call_count, tool_error_count,
                      file_edit_count, file_write_count, active_duration_seconds,
                      duration_seconds
            FROM sessions
            WHERE started_at >= ? {provider_filter}""",
            session_params,
        ).fetchall()

        if not sessions:
            return []

        session_map = {
            (row["provider"], row["session_id"]): dict(row) for row in sessions
        }
        metrics: dict[tuple[str, str], dict] = {
            key: {
                "permission_friction": 0,
                "permission_errors": 0,
                "file_access_errors": 0,
                "edit_mismatch_errors": 0,
                "edit_calls_without_paths": 0,
                "iteration_errors": 0,
                "other_errors": 0,
                "no_prefix_rule_escalations": 0,
                "causes": Counter(),
            }
            for key in session_map
        }

        call_provider_filter = "AND s.provider = ?" if provider else ""
        calls = con.execute(
            f"""SELECT tc.provider, tc.session_id, tc.tool_name, tc.file_path,
                      tc.command, tc.description, tc.is_error
            FROM tool_calls tc
            JOIN sessions s
              ON s.provider = tc.provider
             AND s.session_id = tc.session_id
            WHERE s.started_at >= ? {call_provider_filter}""",
            session_params,
        ).fetchall()

        for row in calls:
            key = (row["provider"], row["session_id"])
            if key not in metrics:
                continue

            if row["tool_name"] in ("Edit", "Write") and row["file_path"] is None:
                metrics[key]["edit_calls_without_paths"] += 1

            is_escalation = _has_escalation_marker(
                row["command"], row["description"],
            )
            category = None
            is_permission_error = False
            if row["is_error"]:
                category = _categorize_error(
                    row["tool_name"], row["command"], row["description"],
                )
                is_permission_error = category == "Permission"

            if is_escalation or is_permission_error:
                metrics[key]["permission_friction"] += 1
                for cause in _permission_friction_causes(
                    row["command"],
                    row["description"],
                    family=_command_family(row["command"]),
                    is_escalation=is_escalation,
                ):
                    metrics[key]["causes"][cause["label"]] += 1

            if is_escalation:
                if _extract_prefix_rule(row["description"]) is None:
                    metrics[key]["no_prefix_rule_escalations"] += 1

            if not row["is_error"]:
                continue

            if category == "Permission":
                metrics[key]["permission_errors"] += 1
            elif category == "File Access":
                metrics[key]["file_access_errors"] += 1
            elif category == "Edit Mismatch":
                metrics[key]["edit_mismatch_errors"] += 1
            elif category in _ITERATION_CATEGORIES:
                metrics[key]["iteration_errors"] += 1
            else:
                metrics[key]["other_errors"] += 1

        rows = []
        for key, session in session_map.items():
            item_metrics = metrics[key]
            edits = (session["file_edit_count"] or 0) + (
                session["file_write_count"] or 0
            )
            active_seconds = session["active_duration_seconds"] or 0
            cost = session["estimated_cost_usd"] or 0.0
            cost_per_edit = cost / edits if edits > 0 else 0.0
            flags, score = _investigation_flags(
                session,
                item_metrics,
                edits=edits,
                active_seconds=active_seconds,
                cost_per_edit=cost_per_edit,
            )
            if score <= 0:
                continue
            rows.append({
                "provider": session["provider"],
                "session_id": session["session_id"],
                "project_path": session["project_path"],
                "project_name": session["project_name"],
                "custom_title": session["custom_title"],
                "started_at": session["started_at"],
                "score": score,
                "flags": flags,
                "causes": [
                    {"label": label, "count": count}
                    for label, count in item_metrics["causes"].most_common(4)
                ],
                "permission_friction": item_metrics["permission_friction"],
                "file_access_errors": item_metrics["file_access_errors"],
                "other_errors": item_metrics["other_errors"],
                "edit_calls_without_paths": item_metrics["edit_calls_without_paths"],
                "tool_error_count": session["tool_error_count"] or 0,
                "tool_call_count": session["tool_call_count"] or 0,
                "edits": edits,
                "active_duration_seconds": active_seconds,
                "cost_per_edit": cost_per_edit,
            })

        rows.sort(
            key=lambda row: (
                row["score"],
                row["permission_friction"],
                row["tool_error_count"],
            ),
            reverse=True,
        )
        return rows[:limit]
    finally:
        con.close()


def get_investigation_action_summary(
    db_path: Path,
    hours: int = 36,
    provider: str | None = None,
) -> dict:
    """Aggregate investigation queue signals into repeat causes and actions."""
    rows = get_investigation_queue(
        db_path,
        hours=hours,
        provider=provider,
        limit=100000,
    )
    flag_counts: dict[str, int] = {}
    flag_tones: dict[str, str] = {}
    cause_counts: dict[str, int] = {}
    project_counts: dict[tuple[str, str], dict] = {}
    total_score = 0

    for row in rows:
        total_score += row["score"]
        project_key = (row["provider"], row["project_name"])
        if project_key not in project_counts:
            project_counts[project_key] = {
                "provider": row["provider"],
                "project_name": row["project_name"],
                "count": 0,
                "score": 0,
            }
        project_counts[project_key]["count"] += 1
        project_counts[project_key]["score"] += row["score"]

        for flag in row["flags"]:
            label = _normalize_investigation_flag_label(flag["label"])
            flag_counts[label] = flag_counts.get(label, 0) + 1
            flag_tones[label] = _stronger_tone(
                flag_tones.get(label),
                flag.get("tone", "gray"),
            )

        for cause in row["causes"]:
            label = cause["label"]
            cause_counts[label] = cause_counts.get(label, 0) + cause["count"]

    flagged_count = len(rows)
    flag_breakdown = [
        {
            "label": label,
            "slug": _investigation_label_slug(label),
            "count": count,
            "pct": count / flagged_count if flagged_count else 0.0,
            "tone": flag_tones.get(label, "gray"),
            "action": _investigation_action_for_label(label),
        }
        for label, count in flag_counts.items()
    ]
    flag_breakdown.sort(key=lambda item: item["count"], reverse=True)

    cause_breakdown = [
        {
            "label": label,
            "slug": _investigation_label_slug(label),
            "count": count,
            "action": _investigation_action_for_label(label),
        }
        for label, count in cause_counts.items()
    ]
    cause_breakdown.sort(key=lambda item: item["count"], reverse=True)

    project_breakdown = list(project_counts.values())
    project_breakdown.sort(key=lambda item: (item["score"], item["count"]), reverse=True)

    actions = [
        {
            "label": item["label"],
            "slug": item["slug"],
            "count": item["count"],
            "action": item["action"],
            "source": "flag",
        }
        for item in flag_breakdown[:6]
    ]
    actions.extend(
        {
            "label": item["label"],
            "slug": item["slug"],
            "count": item["count"],
            "action": item["action"],
            "source": "cause",
        }
        for item in cause_breakdown[:6]
        if item["label"] not in {action["label"] for action in actions}
    )
    actions.sort(key=lambda item: item["count"], reverse=True)

    return {
        "window_hours": hours,
        "flagged_session_count": flagged_count,
        "total_score": total_score,
        "flag_breakdown": flag_breakdown,
        "cause_breakdown": cause_breakdown,
        "project_breakdown": project_breakdown[:8],
        "actions": actions[:6],
    }


def get_effectiveness_overview(
    db_path: Path,
    days: int = 30,
    provider: str | None = None,
) -> dict:
    """Current-vs-previous effectiveness summary for the effectiveness page."""
    buckets = _collect_effectiveness_periods(db_path, days=days, provider=provider)
    current = _effectiveness_bucket_result(buckets[("__all__", "__all__")]["current"])
    previous = _effectiveness_bucket_result(buckets[("__all__", "__all__")]["previous"])
    return {
        "window_days": days,
        "current": current,
        "previous": previous,
        "deltas": {
            "avg_cost_per_session": _ratio_delta(
                current["avg_cost_per_session"],
                previous["avg_cost_per_session"],
            ),
            "avg_active_seconds": _ratio_delta(
                current["avg_active_seconds"],
                previous["avg_active_seconds"],
            ),
            "review_rate": current["review_rate"] - previous["review_rate"],
            "edit_attribution_rate": (
                current["edit_attribution_rate"] - previous["edit_attribution_rate"]
            ),
            "error_rate": current["error_rate"] - previous["error_rate"],
        },
    }


def get_effectiveness_project_rollups(
    db_path: Path,
    days: int = 30,
    provider: str | None = None,
) -> list[dict]:
    """Project/provider effectiveness rows with previous-window comparison."""
    buckets = _collect_effectiveness_periods(db_path, days=days, provider=provider)
    rows = []
    for key, periods in buckets.items():
        if key == ("__all__", "__all__"):
            continue
        current = _effectiveness_bucket_result(periods["current"])
        if current["session_count"] == 0:
            continue
        previous = _effectiveness_bucket_result(periods["previous"])
        rows.append({
            "provider": key[0],
            "project_name": key[1],
            "current": current,
            "previous": previous,
            "deltas": {
                "avg_cost_per_session": _ratio_delta(
                    current["avg_cost_per_session"],
                    previous["avg_cost_per_session"],
                ),
                "avg_active_seconds": _ratio_delta(
                    current["avg_active_seconds"],
                    previous["avg_active_seconds"],
                ),
                "review_rate": current["review_rate"] - previous["review_rate"],
                "edit_attribution_rate": (
                    current["edit_attribution_rate"]
                    - previous["edit_attribution_rate"]
                ),
                "error_rate": current["error_rate"] - previous["error_rate"],
            },
        })

    rows.sort(
        key=lambda row: (
            row["current"]["review_rate"],
            row["current"]["error_rate"],
            row["current"]["avg_cost_per_session"],
            row["current"]["session_count"],
        ),
        reverse=True,
    )
    return rows


def get_effectiveness_daily_trends(
    db_path: Path,
    days: int = 90,
    provider: str | None = None,
) -> list[dict]:
    """Daily effectiveness trend series for charting rates over time."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    con = get_connection(db_path)
    try:
        provider_filter = "AND provider = ?" if provider else ""
        params = (cutoff.isoformat(), provider) if provider else (cutoff.isoformat(),)
        session_rows = con.execute(
            f"""SELECT provider, session_id, project_name, started_at,
                      estimated_cost_usd, active_duration_seconds,
                      tool_call_count, tool_error_count,
                      file_edit_count, file_write_count
            FROM sessions
            WHERE started_at >= ? {provider_filter}
            ORDER BY started_at""",
            params,
        ).fetchall()

        buckets: defaultdict[str, dict] = defaultdict(_empty_effectiveness_bucket)
        for row in session_rows:
            day = row["started_at"][:10]
            _add_session_to_effectiveness_bucket(buckets[day], row)

        joined_provider_filter = "AND s.provider = ?" if provider else ""
        edit_rows = con.execute(
            f"""SELECT s.started_at, tc.file_path
            FROM tool_calls tc
            JOIN sessions s
              ON s.provider = tc.provider
             AND s.session_id = tc.session_id
            WHERE s.started_at >= ? {joined_provider_filter}
              AND tc.tool_name IN ('Edit', 'Write')""",
            params,
        ).fetchall()
        for row in edit_rows:
            _add_edit_call_to_effectiveness_bucket(buckets[row["started_at"][:10]], row)
    finally:
        con.close()

    flagged_rows = get_investigation_queue(
        db_path,
        hours=days * 24,
        provider=provider,
        limit=100000,
    )
    for row in flagged_rows:
        started_at = row.get("started_at")
        if not started_at:
            continue
        bucket = buckets[started_at[:10]]
        bucket["review_session_count"] += 1
        bucket["review_score"] += row["score"]

    return [
        {"date": day, **_effectiveness_bucket_result(bucket)}
        for day, bucket in sorted(buckets.items())
    ]


def _collect_effectiveness_periods(
    db_path: Path,
    days: int,
    provider: str | None,
) -> defaultdict[tuple[str, str], dict[str, dict]]:
    now = datetime.now(timezone.utc)
    current_cutoff = now - timedelta(days=days)
    previous_cutoff = now - timedelta(days=days * 2)
    buckets: defaultdict[tuple[str, str], dict[str, dict]] = defaultdict(
        _empty_effectiveness_periods,
    )

    con = get_connection(db_path)
    try:
        provider_filter = "AND provider = ?" if provider else ""
        params = (
            (previous_cutoff.isoformat(), provider)
            if provider else (previous_cutoff.isoformat(),)
        )
        session_rows = con.execute(
            f"""SELECT provider, session_id, project_name, started_at,
                      estimated_cost_usd, active_duration_seconds,
                      tool_call_count, tool_error_count,
                      file_edit_count, file_write_count
            FROM sessions
            WHERE started_at >= ? {provider_filter}""",
            params,
        ).fetchall()

        for row in session_rows:
            period = _effectiveness_period(
                row["started_at"],
                current_cutoff=current_cutoff,
                previous_cutoff=previous_cutoff,
            )
            if period is None:
                continue
            _add_session_to_effectiveness_bucket(
                buckets[(row["provider"], row["project_name"])][period],
                row,
            )
            _add_session_to_effectiveness_bucket(
                buckets[("__all__", "__all__")][period],
                row,
            )

        joined_provider_filter = "AND s.provider = ?" if provider else ""
        edit_rows = con.execute(
            f"""SELECT s.provider, s.project_name, s.started_at, tc.file_path
            FROM tool_calls tc
            JOIN sessions s
              ON s.provider = tc.provider
             AND s.session_id = tc.session_id
            WHERE s.started_at >= ? {joined_provider_filter}
              AND tc.tool_name IN ('Edit', 'Write')""",
            params,
        ).fetchall()

        for row in edit_rows:
            period = _effectiveness_period(
                row["started_at"],
                current_cutoff=current_cutoff,
                previous_cutoff=previous_cutoff,
            )
            if period is None:
                continue
            _add_edit_call_to_effectiveness_bucket(
                buckets[(row["provider"], row["project_name"])][period],
                row,
            )
            _add_edit_call_to_effectiveness_bucket(
                buckets[("__all__", "__all__")][period],
                row,
            )
    finally:
        con.close()

    flagged_rows = get_investigation_queue(
        db_path,
        hours=days * 48,
        provider=provider,
        limit=100000,
    )
    for row in flagged_rows:
        period = _effectiveness_period(
            row["started_at"],
            current_cutoff=current_cutoff,
            previous_cutoff=previous_cutoff,
        )
        if period is None:
            continue
        for key in ((row["provider"], row["project_name"]), ("__all__", "__all__")):
            buckets[key][period]["review_session_count"] += 1
            buckets[key][period]["review_score"] += row["score"]

    return buckets


def _empty_effectiveness_periods() -> dict[str, dict]:
    return {
        "current": _empty_effectiveness_bucket(),
        "previous": _empty_effectiveness_bucket(),
    }


def _empty_effectiveness_bucket() -> dict:
    return {
        "session_count": 0,
        "total_cost": 0.0,
        "total_active_seconds": 0,
        "tool_call_count": 0,
        "tool_error_count": 0,
        "no_edit_session_count": 0,
        "edit_call_count": 0,
        "attributed_edit_call_count": 0,
        "review_session_count": 0,
        "review_score": 0,
    }


def _add_session_to_effectiveness_bucket(bucket: dict, row: dict) -> None:
    edits = (row["file_edit_count"] or 0) + (row["file_write_count"] or 0)
    bucket["session_count"] += 1
    bucket["total_cost"] += row["estimated_cost_usd"] or 0.0
    bucket["total_active_seconds"] += row["active_duration_seconds"] or 0
    bucket["tool_call_count"] += row["tool_call_count"] or 0
    bucket["tool_error_count"] += row["tool_error_count"] or 0
    if (row["tool_call_count"] or 0) > 0 and edits == 0:
        bucket["no_edit_session_count"] += 1


def _add_edit_call_to_effectiveness_bucket(bucket: dict, row: dict) -> None:
    bucket["edit_call_count"] += 1
    if row["file_path"] is not None:
        bucket["attributed_edit_call_count"] += 1


def _effectiveness_bucket_result(bucket: dict) -> dict:
    sessions = bucket["session_count"]
    total_cost = bucket["total_cost"]
    edit_calls = bucket["edit_call_count"]
    attributed_edits = bucket["attributed_edit_call_count"]
    tools = bucket["tool_call_count"]
    return {
        "session_count": sessions,
        "total_cost": round(total_cost, 4),
        "avg_cost_per_session": total_cost / sessions if sessions > 0 else 0.0,
        "avg_active_seconds": (
            bucket["total_active_seconds"] / sessions if sessions > 0 else 0.0
        ),
        "tool_call_count": tools,
        "tool_error_count": bucket["tool_error_count"],
        "error_rate": bucket["tool_error_count"] / tools if tools > 0 else 0.0,
        "no_edit_session_count": bucket["no_edit_session_count"],
        "no_edit_rate": (
            bucket["no_edit_session_count"] / sessions if sessions > 0 else 0.0
        ),
        "edit_call_count": edit_calls,
        "attributed_edit_call_count": attributed_edits,
        "edit_attribution_rate": (
            attributed_edits / edit_calls if edit_calls > 0 else 0.0
        ),
        "cost_per_edit": total_cost / attributed_edits if attributed_edits > 0 else 0.0,
        "review_session_count": bucket["review_session_count"],
        "review_rate": (
            bucket["review_session_count"] / sessions if sessions > 0 else 0.0
        ),
        "review_score": bucket["review_score"],
    }


def _effectiveness_period(
    started_at: str,
    current_cutoff: datetime,
    previous_cutoff: datetime,
) -> str | None:
    value = _parse_iso_datetime(started_at)
    if value >= current_cutoff:
        return "current"
    if value >= previous_cutoff:
        return "previous"
    return None


def _parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _ratio_delta(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current - previous) / previous


def _investigation_flags(
    session: dict,
    metrics: dict,
    edits: int,
    active_seconds: int,
    cost_per_edit: float,
) -> tuple[list[dict], int]:
    flags = []
    score = 0
    tool_calls = session["tool_call_count"] or 0
    total_errors = session["tool_error_count"] or 0
    duration_seconds = session["duration_seconds"] or 0
    cost = session["estimated_cost_usd"] or 0.0

    if metrics["permission_friction"] >= 3:
        score += min(metrics["permission_friction"] * 3, 30)
        flags.append({
            "label": f"{metrics['permission_friction']} permission friction",
            "tone": "amber",
        })
    if metrics["no_prefix_rule_escalations"] >= 2:
        score += min(metrics["no_prefix_rule_escalations"] * 2, 12)
        flags.append({
            "label": "missing prefix rules",
            "tone": "amber",
        })
    if metrics["permission_errors"] >= 2:
        score += min(metrics["permission_errors"] * 4, 20)
        flags.append({
            "label": f"{metrics['permission_errors']} permission errors",
            "tone": "red",
        })
    if metrics["file_access_errors"] >= 5:
        score += min(metrics["file_access_errors"], 15)
        flags.append({
            "label": f"{metrics['file_access_errors']} file access errors",
            "tone": "red",
        })
    if metrics["edit_mismatch_errors"] >= 3:
        score += min(metrics["edit_mismatch_errors"] * 2, 16)
        flags.append({
            "label": f"{metrics['edit_mismatch_errors']} edit mismatches",
            "tone": "red",
        })
    if metrics["edit_calls_without_paths"] >= 2:
        score += min(metrics["edit_calls_without_paths"] * 4, 20)
        flags.append({
            "label": f"{metrics['edit_calls_without_paths']} edits missing paths",
            "tone": "red",
        })
    if metrics["other_errors"] >= 3:
        score += min(metrics["other_errors"] * 2, 20)
        flags.append({
            "label": f"{metrics['other_errors']} Other errors",
            "tone": "amber",
        })
    if total_errors >= 50:
        score += min(total_errors // 10, 20)
        flags.append({
            "label": f"{total_errors} total errors",
            "tone": "red",
        })
    if tool_calls >= 20 and edits == 0:
        score += 10
        flags.append({"label": "no edits", "tone": "gray"})
    if edits == 0 and cost >= _EXPENSIVE_NO_EDIT_COST_USD:
        score += min(int(cost * 5), 20)
        flags.append({"label": "expensive no-edit", "tone": "amber"})
    if active_seconds == 0 and tool_calls >= 10:
        score += 8
        flags.append({"label": "zero active time", "tone": "gray"})
    elif (
        tool_calls >= 10
        and active_seconds <= _LOW_ACTIVE_TIME_SECONDS
        and (
            duration_seconds == 0
            or active_seconds / max(duration_seconds, 1) <= _LOW_ACTIVE_TIME_RATIO
        )
    ):
        score += 6
        flags.append({"label": "low active time", "tone": "gray"})
    if cost_per_edit >= 5:
        score += min(int(cost_per_edit), 20)
        flags.append({"label": "high cost/edit", "tone": "amber"})
    if _weak_project_attribution(session):
        score += 5
        flags.append({"label": "weak attribution", "tone": "gray"})

    return flags, score


def _normalize_investigation_flag_label(label: str) -> str:
    if label.endswith(" permission friction"):
        return "permission friction"
    if label.endswith(" permission errors"):
        return "permission errors"
    if label.endswith(" file access errors"):
        return "file access errors"
    if label.endswith(" edit mismatches"):
        return "edit mismatches"
    if label.endswith(" edits missing paths"):
        return "edits missing paths"
    if label.endswith(" Other errors"):
        return "Other errors"
    if label.endswith(" total errors"):
        return "high total errors"
    return label


def _investigation_row_matches_signal(row: dict, signal: str) -> bool:
    for flag in row["flags"]:
        label = _normalize_investigation_flag_label(flag["label"])
        if _investigation_label_slug(label) == signal:
            return True
    for cause in row["causes"]:
        if _investigation_label_slug(cause["label"]) == signal:
            return True
    return False


def _investigation_action_labels() -> list[str]:
    return [
        "permission friction",
        "missing prefix rules",
        "permission errors",
        "file access errors",
        "edit mismatches",
        "edits missing paths",
        "Other errors",
        "high total errors",
        "no edits",
        "expensive no-edit",
        "zero active time",
        "low active time",
        "high cost/edit",
        "weak attribution",
        "verification command",
        "broad search",
        "mutation command",
        "browser/system access",
        "external service",
        "cache/home access",
        "other permission friction",
    ]


def _investigation_label_slug(label: str) -> str:
    chars = []
    previous_dash = False
    for char in label.lower():
        if char.isalnum():
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    return "".join(chars).strip("-")


def _stronger_tone(current: str | None, new: str) -> str:
    order = {"gray": 0, "amber": 1, "red": 2}
    if current is None:
        return new
    return new if order.get(new, 0) > order.get(current, 0) else current


def _investigation_action_for_label(label: str) -> str:
    actions = {
        "permission friction": (
            "Review repeated approval prompts and add narrow reusable prefix rules "
            "only for deterministic verification commands."
        ),
        "missing prefix rules": (
            "Add prefix_rule guidance for repeated test, lint, or check commands; "
            "keep broad mutation and external-service commands on request."
        ),
        "permission errors": (
            "Check whether the session started in the intended repo root and whether "
            "sandbox boundaries match the task."
        ),
        "file access errors": (
            "Improve project attribution and path instructions so agents search from "
            "the repo root before escalating."
        ),
        "edit mismatches": (
            "Prefer smaller patches or reread target files immediately before edits."
        ),
        "edits missing paths": (
            "Improve shell edit attribution by preferring structured edit tools or "
            "repo-relative paths in mutation commands."
        ),
        "Other errors": (
            "Inspect a sample of uncategorized failures and add a narrow parser category "
            "when a repeat pattern is real."
        ),
        "high total errors": (
            "Separate expected verification failures from setup or environment failures "
            "before treating the session as productive iteration."
        ),
        "no edits": (
            "Classify research-only sessions explicitly or tighten task prompts toward "
            "a concrete code or documentation change."
        ),
        "expensive no-edit": (
            "Review whether the task should have been split into a research brief before "
            "implementation work."
        ),
        "zero active time": (
            "Check provider timing data before using duration metrics for this session."
        ),
        "low active time": (
            "Verify active-time detection for sessions with many tool calls in short work blocks."
        ),
        "high cost/edit": (
            "Look for broad exploration before edits; turn repeat findings into project briefs "
            "or durable artifacts."
        ),
        "weak attribution": (
            "Start agents from the repo root and add project-specific instructions where logs "
            "fall back to generic directories."
        ),
        "verification command": (
            "Persist narrow approvals for repeated deterministic verification commands."
        ),
        "broad search": (
            "Scope search commands to repo-relative paths before requesting broader access."
        ),
        "mutation command": (
            "Keep broad mutation commands on-request unless they are wrapped by a project task."
        ),
        "browser/system access": (
            "Use targeted browser or process checks and avoid broad system-control approvals."
        ),
        "external service": (
            "Keep network and hosted-service commands explicit unless a workflow is proven safe."
        ),
        "cache/home access": (
            "Prefer repo-local caches and avoid exposing home-directory paths unless needed."
        ),
        "other permission friction": (
            "Inspect representative sessions and decide whether a new permission "
            "category is needed."
        ),
    }
    return actions.get(
        label,
        "Review representative sessions and decide whether to update project instructions.",
    )


def _weak_project_attribution(session: dict) -> bool:
    project_name = (session["project_name"] or "").strip().lower()
    project_path = (session["project_path"] or "").strip()
    if not project_name or project_name in _WEAK_PROJECT_NAMES:
        return True
    if project_name in {"unknown", "untitled", "sessions"}:
        return True
    if project_path.endswith("/.codex") or project_path.endswith("/.claude"):
        return True
    return False


def _has_escalation_marker(command: str | None, description: str | None) -> bool:
    text = " ".join(part for part in (command, description) if part).lower()
    return any(marker in text for marker in _ESCALATION_MARKERS)


def _extract_prefix_rule(description: str | None) -> str | None:
    if not description or "prefix_rule=" not in description:
        return None
    value = description.split("prefix_rule=", 1)[1].split(";", 1)[0].strip()
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return value or None
    if isinstance(parsed, list):
        return " ".join(str(part) for part in parsed)
    return str(parsed) if parsed is not None else None


def _command_family(command: str | None) -> str:
    if not command:
        return "no command"
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    parts = _strip_env_assignments(parts)
    if not parts:
        return "env"

    first = _Path(parts[0]).name
    if first in {"python", "python3"} and len(parts) > 2 and parts[1] == "-m":
        return f"python -m {parts[2]}"
    if first == "uv" and len(parts) > 1:
        return f"uv {parts[1]}"
    if first in {"npm", "yarn", "pnpm"} and len(parts) > 1:
        if parts[1] == "--workspace" and len(parts) > 3:
            return f"{first} --workspace {parts[3]}"
        return f"{first} {parts[1]}"
    if first == "git" and len(parts) > 1:
        return f"git {parts[1]}"
    if first in {"gh", "railway", "modal", "make", "just"} and len(parts) > 1:
        return f"{first} {parts[1]}"
    return first


def _allowlist_candidate(command: str | None) -> dict | None:
    if not command:
        return None
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    parts = _strip_env_assignments(parts)
    if not parts:
        return None

    first = _Path(parts[0]).name
    if first == "uv" and len(parts) >= 3 and parts[1] == "run":
        if parts[2] == "pytest":
            return {
                "prefix_rule": "uv run pytest",
                "reason": "Project test command; repeat approvals are usually low-value friction.",
            }
        if parts[2] == "ruff":
            return {
                "prefix_rule": "uv run ruff",
                "reason": "Project lint command; deterministic and repo-local in normal use.",
            }

    if first in {"pytest", "ruff"}:
        return {
            "prefix_rule": first,
            "reason": "Direct test/lint command; deterministic and repo-local in normal use.",
        }

    if first in {"just", "make"} and len(parts) >= 2 and parts[1] in {
        "check", "test", "lint",
    }:
        return {
            "prefix_rule": f"{first} {parts[1]}",
            "reason": "Named project verification task.",
        }

    if first in {"npm", "yarn", "pnpm"}:
        script = _npm_script_name(parts)
        if script in {"build", "check", "lint", "test", "typecheck"}:
            return {
                "prefix_rule": f"{first} run {script}",
                "reason": "Package verification script; useful to persist when run repeatedly.",
            }

    return None


def _permission_friction_causes(
    command: str | None,
    description: str | None,
    family: str | None = None,
    is_escalation: bool = False,
) -> list[dict]:
    family = family or _command_family(command)
    text = " ".join(part for part in (command, description) if part).lower()
    causes = []

    if is_escalation and _extract_prefix_rule(description) is None:
        causes.append({
            "label": "missing prefix rule",
            "reason": "Escalation did not include a reusable narrow prefix rule.",
        })
    if family in _VERIFICATION_FAMILIES or _allowlist_candidate(command) is not None:
        causes.append({
            "label": "verification command",
            "reason": "Test, lint, build, or check command.",
        })
    elif family in _BROAD_SEARCH_FAMILIES:
        causes.append({
            "label": "broad search",
            "reason": "Search command that may cross repo or sandbox boundaries.",
        })
    elif family in _MUTATION_FAMILIES:
        causes.append({
            "label": "mutation command",
            "reason": "Command family can edit, copy, or execute arbitrary code.",
        })

    if family in _BROWSER_SYSTEM_FAMILIES or _contains_browser_system_text(text):
        _append_cause_once(causes, {
            "label": "browser/system access",
            "reason": "Command touches browser, process, OS, or system-control surfaces.",
        })
    if family in _EXTERNAL_SERVICE_FAMILIES or _contains_external_service_text(text):
        _append_cause_once(causes, {
            "label": "external service",
            "reason": "Command talks to a network, hosted service, or external CLI.",
        })

    if _contains_cache_or_home_text(text):
        causes.append({
            "label": "cache/home access",
            "reason": "Command references home-directory or package-cache locations.",
        })

    if not causes:
        causes.append({
            "label": "other permission friction",
            "reason": "Permission-related event outside common command families.",
        })

    return causes


def _append_cause_once(causes: list[dict], cause: dict) -> None:
    if not any(item["label"] == cause["label"] for item in causes):
        causes.append(cause)


def _contains_browser_system_text(text: str) -> bool:
    return any(
        token in text
        for token in (
            "chrome", "playwright", "browser", "launchctl", "osascript",
            "system_chrome", "lsof", "pgrep",
        )
    )


def _contains_external_service_text(text: str) -> bool:
    return any(
        token in text
        for token in (
            "http://", "https://", "railway", "vercel", "github", "gh ",
            "curl ", "wget ", "ssh ", "scp ",
        )
    )


def _contains_cache_or_home_text(text: str) -> bool:
    return any(
        token in text
        for token in (
            "~/.cache", "/.cache/", "/users/", "$home", "node_modules",
            ".venv", "/site-packages/",
        )
    )


def _npm_script_name(parts: list[str]) -> str | None:
    if len(parts) >= 3 and parts[1] == "run":
        return parts[2]
    if len(parts) >= 5 and parts[1] == "--workspace" and parts[3] == "run":
        return parts[4]
    return None


def _strip_env_assignments(parts: list[str]) -> list[str]:
    index = 0
    if parts and parts[0] == "env":
        index = 1
    while index < len(parts) and _looks_env_assignment(parts[index]):
        index += 1
    return parts[index:]


def _looks_env_assignment(value: str) -> bool:
    if "=" not in value or value.startswith("="):
        return False
    key = value.split("=", 1)[0]
    return key.replace("_", "").isalnum()


def _counter_rows(counts: dict[str, int], label_key: str) -> list[dict]:
    total = sum(counts.values())
    rows = [
        {label_key: key, "count": count, "pct": count / total if total else 0.0}
        for key, count in counts.items()
    ]
    rows.sort(key=lambda row: row["count"], reverse=True)
    return rows


def _increment_policy_bucket(
    buckets: dict[str, dict],
    key: str,
    reason: str,
) -> None:
    if key not in buckets:
        buckets[key] = {"label": key, "reason": reason, "count": 0}
    buckets[key]["count"] += 1


def _increment_cause_bucket(buckets: dict[str, dict], cause: dict) -> None:
    label = cause["label"]
    if label not in buckets:
        buckets[label] = {
            "label": label,
            "reason": cause["reason"],
            "count": 0,
        }
    buckets[label]["count"] += 1


def _policy_rows(buckets: dict[str, dict]) -> list[dict]:
    rows = list(buckets.values())
    rows.sort(key=lambda row: row["count"], reverse=True)
    return rows


def get_error_breakdown(db_path: Path, provider: str | None = None) -> list[dict]:
    """Categorized error breakdown across all sessions.

    Returns:
        [{category, count, pct}, ...] sorted by count desc.
        Empty list if no errors.
    """
    con = get_connection(db_path)
    try:
        provider_filter = "AND provider = ?" if provider else ""
        params = (provider,) if provider else ()
        rows = con.execute(
            f"""SELECT tool_name, command, description
            FROM tool_calls
            WHERE is_error = 1 {provider_filter}""",
            params,
        ).fetchall()

        if not rows:
            return []

        categories: dict[str, int] = {}
        for r in rows:
            cat = _categorize_error(r["tool_name"], r["command"], r["description"])
            categories[cat] = categories.get(cat, 0) + 1

        total = sum(categories.values())
        result = [
            {"category": cat, "count": count, "pct": count / total}
            for cat, count in categories.items()
        ]
        result.sort(key=lambda x: x["count"], reverse=True)
        return result
    finally:
        con.close()


def get_error_breakdown_for_session(
    db_path: Path,
    session_id: str,
    provider: str = "claude",
) -> list[dict]:
    """Categorized error breakdown for a single session.

    Returns:
        [{category, count}, ...] sorted by count desc.
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT tool_name, command, description
            FROM tool_calls
            WHERE is_error = 1 AND provider = ? AND session_id = ?""",
            (provider, session_id),
        ).fetchall()

        if not rows:
            return []

        categories: dict[str, int] = {}
        for r in rows:
            cat = _categorize_error(r["tool_name"], r["command"], r["description"])
            categories[cat] = categories.get(cat, 0) + 1

        result = [
            {"category": cat, "count": count}
            for cat, count in categories.items()
        ]
        result.sort(key=lambda x: x["count"], reverse=True)
        return result
    finally:
        con.close()


def get_artifacts_list(
    db_path: Path,
    project_name: str | None = None,
    status: str | None = None,
    artifact_type: str | None = None,
) -> list[dict]:
    """List semantic artifacts for dashboard review."""
    clauses = []
    params = []
    if project_name:
        clauses.append("project_name = ?")
        params.append(project_name)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if artifact_type:
        clauses.append("artifact_type = ?")
        params.append(artifact_type)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    con = get_connection(db_path)
    try:
        rows = con.execute(
            f"""SELECT
                a.*,
                COUNT(e.id) AS evidence_count,
                GROUP_CONCAT(
                    e.evidence_kind || ': ' || e.summary,
                    CHAR(10)
                ) AS evidence_summaries
            FROM semantic_artifacts a
            LEFT JOIN artifact_evidence e ON e.artifact_id = a.id
            {where}
            GROUP BY a.id
            ORDER BY
                CASE a.status
                    WHEN 'proposed' THEN 0
                    WHEN 'accepted' THEN 1
                    WHEN 'rejected' THEN 2
                    ELSE 3
                END,
                a.updated_at DESC,
                a.id DESC""",
            tuple(params),
        ).fetchall()
        return [
            {
                **dict(row),
                "evidence": _split_evidence(row["evidence_summaries"]),
            }
            for row in rows
        ]
    finally:
        con.close()


def get_artifact_filter_options(db_path: Path) -> dict:
    """Return project/type/status options present in artifact data."""
    con = get_connection(db_path)
    try:
        projects = [
            row["project_name"]
            for row in con.execute(
                """SELECT DISTINCT project_name FROM semantic_artifacts
                ORDER BY project_name"""
            ).fetchall()
        ]
        statuses = [
            row["status"]
            for row in con.execute(
                """SELECT DISTINCT status FROM semantic_artifacts
                ORDER BY status"""
            ).fetchall()
        ]
        types = [
            row["artifact_type"]
            for row in con.execute(
                """SELECT DISTINCT artifact_type FROM semantic_artifacts
                ORDER BY artifact_type"""
            ).fetchall()
        ]
        return {"projects": projects, "statuses": statuses, "types": types}
    finally:
        con.close()


def get_accepted_artifact_projects(db_path: Path) -> list[dict]:
    """Return projects that have accepted artifacts."""
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT project_name, COUNT(*) AS artifact_count
            FROM semantic_artifacts
            WHERE status = 'accepted'
            GROUP BY project_name
            ORDER BY project_name"""
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def _split_evidence(value: str | None) -> list[str]:
    if not value:
        return []
    return [line for line in value.splitlines() if line]
