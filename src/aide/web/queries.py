"""SQL query functions for the dashboard — all reads, no writes.

Each function takes db_path as first argument, opens a connection,
runs queries, and returns plain dicts/lists. Connections are always
closed in a finally block.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from aide.db import get_connection


def get_overview_summary(db_path: Path) -> dict:
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

        row_30d = con.execute(
            """SELECT
                COUNT(*) AS sessions,
                COALESCE(SUM(estimated_cost_usd), 0) AS cost,
                COUNT(DISTINCT project_name) AS projects
            FROM sessions
            WHERE date(started_at) >= ?""",
            (thirty_days_ago,),
        ).fetchone()

        row_week = con.execute(
            """SELECT
                COUNT(*) AS sessions,
                COALESCE(SUM(estimated_cost_usd), 0) AS cost,
                COUNT(DISTINCT project_name) AS projects
            FROM sessions
            WHERE date(started_at) >= ?""",
            (week_start,),
        ).fetchone()

        row_today = con.execute(
            """SELECT
                COUNT(*) AS sessions,
                COALESCE(SUM(estimated_cost_usd), 0) AS cost
            FROM sessions
            WHERE date(started_at) = ?""",
            (today_str,),
        ).fetchone()

        return {
            "last_30d": {
                "sessions": row_30d["sessions"],
                "cost": row_30d["cost"],
                "projects": row_30d["projects"],
            },
            "this_week": {
                "sessions": row_week["sessions"],
                "cost": row_week["cost"],
                "projects": row_week["projects"],
            },
            "today": {
                "sessions": row_today["sessions"],
                "cost": row_today["cost"],
            },
        }
    finally:
        con.close()


def get_daily_cost_series(db_path: Path, days: int = 90) -> list[dict]:
    """Daily cost with 7-day moving average.

    Uses daily_stats where project_name IS NULL (aggregate rows).

    Returns:
        [{date, cost, cost_7d_avg}, ...]
    """
    con = get_connection(db_path)
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = con.execute(
            """SELECT date, estimated_cost_usd AS cost
            FROM daily_stats
            WHERE project_name IS NULL AND date >= ?
            ORDER BY date""",
            (cutoff,),
        ).fetchall()

        result = []
        costs = []
        for row in rows:
            cost = row["cost"] or 0.0
            costs.append(cost)
            window = costs[-7:]
            avg = sum(window) / len(window)
            result.append({
                "date": row["date"],
                "cost": round(cost, 4),
                "cost_7d_avg": round(avg, 4),
            })

        return result
    finally:
        con.close()


def get_weekly_session_counts(db_path: Path, weeks: int = 12) -> list[dict]:
    """Session counts grouped by ISO week.

    Returns:
        [{week_start, session_count}, ...]
    """
    con = get_connection(db_path)
    try:
        cutoff = (date.today() - timedelta(weeks=weeks)).isoformat()
        rows = con.execute(
            """SELECT
                -- SQLite: date(started_at, 'weekday 0', '-6 days') gives Monday
                date(started_at, 'weekday 1', '-7 days') AS week_start,
                COUNT(*) AS session_count
            FROM sessions
            WHERE date(started_at) >= ?
            GROUP BY week_start
            ORDER BY week_start""",
            (cutoff,),
        ).fetchall()

        return [{"week_start": r["week_start"], "session_count": r["session_count"]} for r in rows]
    finally:
        con.close()


def get_cost_by_project(db_path: Path) -> list[dict]:
    """Total cost per project, sorted descending.

    Returns:
        [{project_name, total_cost}, ...]
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT
                project_name,
                COALESCE(SUM(estimated_cost_usd), 0) AS total_cost
            FROM sessions
            GROUP BY project_name
            ORDER BY total_cost DESC"""
        ).fetchall()

        return [{"project_name": r["project_name"], "total_cost": r["total_cost"]} for r in rows]
    finally:
        con.close()


def get_token_breakdown(db_path: Path) -> dict:
    """Total token counts across all sessions.

    Returns:
        {input, output, cache_read, cache_creation}
    """
    con = get_connection(db_path)
    try:
        row = con.execute(
            """SELECT
                COALESCE(SUM(total_input_tokens), 0) AS input,
                COALESCE(SUM(total_output_tokens), 0) AS output,
                COALESCE(SUM(total_cache_read_tokens), 0) AS cache_read,
                COALESCE(SUM(total_cache_creation_tokens), 0) AS cache_creation
            FROM sessions"""
        ).fetchone()

        return {
            "input": row["input"],
            "output": row["output"],
            "cache_read": row["cache_read"],
            "cache_creation": row["cache_creation"],
        }
    finally:
        con.close()


def get_projects_table(db_path: Path) -> list[dict]:
    """Project summary table for the projects page.

    Returns:
        [{project_name, session_count, total_cost, avg_cost_per_session,
          total_duration_seconds, total_tokens}, ...]
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT
                project_name,
                COUNT(*) AS session_count,
                COALESCE(SUM(estimated_cost_usd), 0) AS total_cost,
                COALESCE(AVG(estimated_cost_usd), 0) AS avg_cost_per_session,
                COALESCE(SUM(duration_seconds), 0) AS total_duration_seconds,
                COALESCE(SUM(total_input_tokens) + SUM(total_output_tokens)
                    + SUM(total_cache_read_tokens) + SUM(total_cache_creation_tokens), 0)
                    AS total_tokens
            FROM sessions
            GROUP BY project_name
            ORDER BY total_cost DESC"""
        ).fetchall()

        return [
            {
                "project_name": r["project_name"],
                "session_count": r["session_count"],
                "total_cost": r["total_cost"],
                "avg_cost_per_session": r["avg_cost_per_session"],
                "total_duration_seconds": r["total_duration_seconds"],
                "total_tokens": r["total_tokens"],
            }
            for r in rows
        ]
    finally:
        con.close()


def get_session_scatter_data(db_path: Path) -> list[dict]:
    """Scatter plot data: each session as a point.

    Returns:
        [{session_id, project_name, estimated_cost_usd, started_at}, ...]
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT session_id, project_name, estimated_cost_usd, started_at
            FROM sessions
            ORDER BY started_at"""
        ).fetchall()

        return [
            {
                "session_id": r["session_id"],
                "project_name": r["project_name"],
                "estimated_cost_usd": r["estimated_cost_usd"],
                "started_at": r["started_at"],
            }
            for r in rows
        ]
    finally:
        con.close()


def get_sessions_list(db_path: Path, project_name: str | None = None) -> list[dict]:
    """Session list for the sessions page.

    Args:
        db_path: Path to SQLite database.
        project_name: Optional filter by project name.

    Returns:
        [{session_id, project_name, started_at, duration_seconds,
          message_count, tool_call_count, estimated_cost_usd}, ...]
    """
    con = get_connection(db_path)
    try:
        query = """SELECT
                session_id, project_name, started_at, duration_seconds,
                message_count, tool_call_count, estimated_cost_usd
            FROM sessions"""
        params: tuple = ()

        if project_name:
            query += " WHERE project_name = ?"
            params = (project_name,)

        query += " ORDER BY started_at DESC"

        rows = con.execute(query, params).fetchall()

        return [
            {
                "session_id": r["session_id"],
                "project_name": r["project_name"],
                "started_at": r["started_at"],
                "duration_seconds": r["duration_seconds"],
                "message_count": r["message_count"],
                "tool_call_count": r["tool_call_count"],
                "estimated_cost_usd": r["estimated_cost_usd"],
            }
            for r in rows
        ]
    finally:
        con.close()


def get_session_detail(db_path: Path, session_id: str) -> dict | None:
    """Full detail for a single session.

    Returns:
        Dict with all session fields plus:
        - tool_usage: [{tool_name, count}, ...]
        - files_touched: [{file_path, read_count, edit_count, write_count, total}, ...]
        Returns None if session not found.
    """
    con = get_connection(db_path)
    try:
        session = con.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

        if session is None:
            return None

        detail = dict(session)

        # Tool usage breakdown
        tool_rows = con.execute(
            """SELECT tool_name, COUNT(*) AS count
            FROM tool_calls
            WHERE session_id = ?
            GROUP BY tool_name
            ORDER BY count DESC""",
            (session_id,),
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
            WHERE session_id = ? AND file_path IS NOT NULL""",
            (session_id,),
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

        return detail
    finally:
        con.close()


def get_tool_counts(db_path: Path) -> list[dict]:
    """Total usage count per tool, sorted descending.

    Returns:
        [{tool_name, count}, ...]
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT tool_name, COUNT(*) AS count
            FROM tool_calls
            GROUP BY tool_name
            ORDER BY count DESC"""
        ).fetchall()

        return [{"tool_name": r["tool_name"], "count": r["count"]} for r in rows]
    finally:
        con.close()


def get_tool_weekly(db_path: Path, weeks: int = 12) -> list[dict]:
    """Tool usage grouped by week and tool name.

    Returns:
        [{week_start, tool_name, count}, ...]
    """
    con = get_connection(db_path)
    try:
        cutoff = (date.today() - timedelta(weeks=weeks)).isoformat()
        rows = con.execute(
            """SELECT
                date(timestamp, 'weekday 1', '-7 days') AS week_start,
                tool_name,
                COUNT(*) AS count
            FROM tool_calls
            WHERE date(timestamp) >= ?
            GROUP BY week_start, tool_name
            ORDER BY week_start, count DESC""",
            (cutoff,),
        ).fetchall()

        return [
            {"week_start": r["week_start"], "tool_name": r["tool_name"], "count": r["count"]}
            for r in rows
        ]
    finally:
        con.close()


def get_top_files(db_path: Path, limit: int = 20) -> list[dict]:
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
        rows = con.execute(
            """SELECT tool_name, file_path
            FROM tool_calls
            WHERE file_path IS NOT NULL
                AND tool_name IN ('Read', 'Glob', 'Grep', 'Edit', 'Write')"""
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
