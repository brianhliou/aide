"""SQL query functions for the dashboard — all reads, no writes.

Each function takes db_path as first argument, opens a connection,
runs queries, and returns plain dicts/lists. Connections are always
closed in a finally block.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from aide.db import get_connection

# ---------------------------------------------------------------------------
# Error categorization
# ---------------------------------------------------------------------------

# Categories: Test, Lint, Build, Git, Edit Mismatch, File Access, Other
_BASH_TEST_KW = ("pytest", "python -m pytest", "jest ", "mocha ", "cargo test", "go test")
_BASH_LINT_KW = ("ruff", "mypy", "flake8", "eslint", "prettier", "black ", "isort")
_BASH_BUILD_KW = ("pip ", "uv pip", "npm ", "yarn ", "cargo build", "make ")


def _categorize_error(tool_name: str, command: str | None) -> str:
    """Classify a tool error into a human-readable category."""
    if tool_name == "Edit":
        return "Edit Mismatch"
    if tool_name in ("Read", "Write", "Glob", "Grep"):
        return "File Access"
    if tool_name != "Bash" or not command:
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
    return "Other"


# These are expected iteration errors, not mistakes
_ITERATION_CATEGORIES = frozenset({"Test", "Lint", "Build"})


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

        # Work block counts for each period
        wb_30d = con.execute(
            "SELECT COUNT(*) AS n FROM work_blocks WHERE date(started_at) >= ?",
            (thirty_days_ago,),
        ).fetchone()
        wb_week = con.execute(
            "SELECT COUNT(*) AS n FROM work_blocks WHERE date(started_at) >= ?",
            (week_start,),
        ).fetchone()
        wb_today = con.execute(
            "SELECT COUNT(*) AS n FROM work_blocks WHERE date(started_at) = ?",
            (today_str,),
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


def get_weekly_work_block_counts(db_path: Path, weeks: int = 12) -> list[dict]:
    """Work block counts grouped by ISO week.

    Returns:
        [{week_start, work_block_count}, ...]
    """
    con = get_connection(db_path)
    try:
        cutoff = (date.today() - timedelta(weeks=weeks)).isoformat()
        rows = con.execute(
            """SELECT
                date(started_at, 'weekday 1', '-7 days') AS week_start,
                COUNT(*) AS work_block_count
            FROM work_blocks
            WHERE date(started_at) >= ?
            GROUP BY week_start
            ORDER BY week_start""",
            (cutoff,),
        ).fetchall()

        return [
            {"week_start": r["week_start"], "work_block_count": r["work_block_count"]}
            for r in rows
        ]
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
                s.session_id, s.project_name, s.started_at, s.duration_seconds,
                s.active_duration_seconds,
                s.message_count, s.user_message_count, s.tool_call_count,
                s.estimated_cost_usd, s.total_input_tokens, s.total_output_tokens,
                s.total_cache_read_tokens, s.total_cache_creation_tokens,
                s.file_edit_count, s.file_write_count, s.compaction_count,
                s.custom_title, s.tool_error_count,
                (SELECT COUNT(*) FROM work_blocks wb
                 WHERE wb.session_id = s.session_id) AS work_block_count
            FROM sessions s"""
        params: tuple = ()

        if project_name:
            query += " WHERE s.project_name = ?"
            params = (project_name,)

        query += " ORDER BY s.started_at DESC"

        rows = con.execute(query, params).fetchall()

        return [
            {
                "session_id": r["session_id"],
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

        # File focus ratio
        focus_row = con.execute(
            """SELECT COUNT(DISTINCT file_path) AS unique_files,
                      COUNT(*) AS total_ops
            FROM tool_calls
            WHERE session_id = ? AND file_path IS NOT NULL""",
            (session_id,),
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
            WHERE session_id = ? AND role = 'user'
            ORDER BY id LIMIT 1""",
            (session_id,),
        ).fetchone()
        detail["first_prompt_len"] = (
            first_prompt["content_length"] if first_prompt else 0
        )

        # Work blocks for this session
        wb_rows = con.execute(
            """SELECT block_index, started_at, ended_at,
                      duration_seconds, message_count
            FROM work_blocks
            WHERE session_id = ?
            ORDER BY block_index""",
            (session_id,),
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
                """SELECT tool_name, command
                FROM tool_calls
                WHERE is_error = 1 AND session_id = ?""",
                (session_id,),
            ).fetchall()
            categories: dict[str, int] = {}
            for r in err_rows:
                cat = _categorize_error(r["tool_name"], r["command"])
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


def get_tool_daily(db_path: Path, days: int = 90) -> list[dict]:
    """Tool usage grouped by day and tool name.

    Returns:
        [{date, tool_name, count}, ...]
    """
    con = get_connection(db_path)
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = con.execute(
            """SELECT
                date(timestamp) AS date,
                tool_name,
                COUNT(*) AS count
            FROM tool_calls
            WHERE date(timestamp) >= ?
            GROUP BY date, tool_name
            ORDER BY date, count DESC""",
            (cutoff,),
        ).fetchall()

        return [
            {"date": r["date"], "tool_name": r["tool_name"], "count": r["count"]}
            for r in rows
        ]
    finally:
        con.close()


def get_effectiveness_summary(db_path: Path) -> dict:
    """Effectiveness metrics aggregated across all sessions.

    Returns 9 metrics derived from exact token/tool counts, plus
    iteration_error_pct (fraction of errors that are test/lint/build):
        {cache_hit_rate, edit_ratio, compaction_rate, read_to_edit_ratio,
         output_ratio, tokens_per_user_msg, turns_per_user_prompt,
         error_rate, iteration_error_pct, rework_rate, session_count}
    """
    con = get_connection(db_path)
    try:
        row = con.execute(
            """SELECT
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
            FROM sessions"""
        ).fetchone()

        n = row["session_count"]
        cache_read = row["total_cache_read"]
        input_denom = row["total_input"] + cache_read + row["total_cache_creation"]
        edit_write = row["total_edits"] + row["total_writes"]
        io_total = row["total_input"] + row["total_output"]
        all_tokens = input_denom + row["total_output"]
        user_msgs = row["total_user_msgs"]
        total_errors = row["total_errors"]

        # Categorize errors to compute iteration percentage
        iteration_errors = 0
        if total_errors > 0:
            err_rows = con.execute(
                """SELECT tool_name, command
                FROM tool_calls
                WHERE is_error = 1"""
            ).fetchall()
            for r in err_rows:
                cat = _categorize_error(r["tool_name"], r["command"])
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


def get_effectiveness_trends(db_path: Path, days: int = 90) -> list[dict]:
    """Per-session effectiveness metrics for trend charts.

    Returns:
        [{date, session_id, cache_hit_rate, edit_ratio, had_compaction}, ...]
        Sorted by started_at ascending.
    """
    con = get_connection(db_path)
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = con.execute(
            """SELECT
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
            WHERE date(started_at) >= ?
            ORDER BY started_at""",
            (cutoff,),
        ).fetchall()

        result = []
        for r in rows:
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


def get_top_bash_commands(db_path: Path, limit: int = 20) -> list[dict]:
    """Most common Bash commands with error rates.

    Returns:
        [{command, count, error_count, error_rate}, ...]
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT
                command,
                COUNT(*) AS count,
                SUM(is_error) AS error_count
            FROM tool_calls
            WHERE tool_name = 'Bash' AND command IS NOT NULL
            GROUP BY command
            ORDER BY count DESC
            LIMIT ?""",
            (limit,),
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


def get_first_prompt_analysis(db_path: Path) -> dict:
    """Analyze correlation between first prompt length and session outcomes.

    Returns:
        {buckets: [{label, n, avg_cost, avg_errors, avg_edits}, ...],
         scatter: [{prompt_len, cost, errors}, ...]}
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT s.session_id, s.estimated_cost_usd, s.tool_error_count,
                      s.file_edit_count + s.file_write_count AS edits,
                      m.content_length AS first_prompt_len
            FROM sessions s
            JOIN messages m ON m.session_id = s.session_id
            WHERE m.role = 'user'
              AND m.id = (
                  SELECT MIN(id) FROM messages
                  WHERE session_id = s.session_id AND role = 'user'
              )
              AND m.content_length > 0"""
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


def get_cost_concentration(db_path: Path) -> dict:
    """Cost distribution across sessions (Pareto analysis).

    Returns:
        {sessions: [{session_id, project_name, cost, cumulative_pct,
                     custom_title, started_at}, ...],
         top3_pct: float, median: float, p90: float}
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT session_id, project_name, estimated_cost_usd,
                      custom_title, started_at
            FROM sessions
            WHERE estimated_cost_usd > 0
            ORDER BY estimated_cost_usd DESC"""
        ).fetchall()

        if not rows:
            return {"sessions": [], "top3_pct": 0, "median": 0, "p90": 0}

        total_cost = sum(r["estimated_cost_usd"] for r in rows)
        cumulative = 0.0
        sessions = []
        for r in rows:
            cumulative += r["estimated_cost_usd"]
            sessions.append({
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


def get_cost_per_edit_by_duration(db_path: Path) -> list[dict]:
    """Cost per edit grouped by session duration bucket.

    Returns:
        [{label, n, avg_cost_per_edit, total_cost, total_edits}, ...]
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT active_duration_seconds AS duration_seconds,
                      estimated_cost_usd,
                      file_edit_count + file_write_count AS edits
            FROM sessions
            WHERE (file_edit_count + file_write_count) > 0
              AND estimated_cost_usd > 0"""
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


def get_model_breakdown(db_path: Path) -> list[dict]:
    """Token usage and estimated cost per model.

    Returns:
        [{model, msg_count, input_tokens, output_tokens, cache_tokens,
          estimated_cost}, ...]
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT model,
                      COUNT(*) AS msg_count,
                      COALESCE(SUM(input_tokens), 0) AS input_tokens,
                      COALESCE(SUM(output_tokens), 0) AS output_tokens,
                      COALESCE(SUM(cache_read_tokens), 0) AS cache_tokens
            FROM messages
            WHERE model IS NOT NULL AND model != '<synthetic>'
            GROUP BY model
            ORDER BY SUM(output_tokens) DESC"""
        ).fetchall()

        result = []
        for r in rows:
            # Estimate cost at Sonnet rates
            cost = (
                r["input_tokens"] * 3 / 1e6
                + r["output_tokens"] * 15 / 1e6
                + r["cache_tokens"] * 0.3 / 1e6
            )
            result.append({
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


def get_tool_sequences(db_path: Path, limit: int = 15) -> list[dict]:
    """Most common tool→tool transitions.

    Returns:
        [{from_tool, to_tool, count}, ...]
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT session_id, tool_name
            FROM tool_calls
            ORDER BY session_id, timestamp, id"""
        ).fetchall()

        from collections import Counter
        transitions: Counter[tuple[str, str]] = Counter()
        prev_tool = None
        prev_sess = None
        for r in rows:
            if r["session_id"] == prev_sess and prev_tool:
                transitions[(prev_tool, r["tool_name"])] += 1
            prev_tool = r["tool_name"]
            prev_sess = r["session_id"]

        return [
            {"from_tool": a, "to_tool": b, "count": c}
            for (a, b), c in transitions.most_common(limit)
        ]
    finally:
        con.close()


def get_time_patterns(db_path: Path) -> dict:
    """Work block start patterns by hour-of-day and day-of-week.

    Uses work_blocks table so patterns reflect actual sit-down coding time,
    not misleading session start times for multi-day sessions.

    Returns:
        {by_hour: [{hour, count}, ...],
         by_day: [{day, day_name, count}, ...]}
    """
    con = get_connection(db_path)
    try:
        hour_rows = con.execute(
            """SELECT
                CAST(substr(started_at, 12, 2) AS INTEGER) AS hour,
                COUNT(*) AS count
            FROM work_blocks
            GROUP BY hour
            ORDER BY hour"""
        ).fetchall()

        day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        day_rows = con.execute(
            """SELECT
                CAST(strftime('%w', started_at) AS INTEGER) AS day_num,
                COUNT(*) AS count
            FROM work_blocks
            GROUP BY day_num
            ORDER BY day_num"""
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


def get_user_response_times(db_path: Path) -> dict:
    """Distribution of user response times (gap between assistant and next user msg).

    Returns:
        {median_seconds: float, mean_seconds: float, count: int,
         buckets: [{label, count, pct}, ...]}
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT session_id, role, timestamp
            FROM messages
            WHERE role IN ('user', 'assistant')
            ORDER BY session_id, timestamp"""
        ).fetchall()

        from datetime import datetime as dt
        gaps = []
        prev_role = None
        prev_ts = None
        prev_sess = None
        for r in rows:
            if (r["session_id"] == prev_sess
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
            prev_sess = r["session_id"]

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


def get_thinking_stats(db_path: Path) -> dict:
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
        rows = con.execute(
            """SELECT session_id, project_name, custom_title, started_at,
                      total_thinking_chars, thinking_message_count,
                      estimated_cost_usd, tool_error_count
            FROM sessions
            WHERE total_thinking_chars > 0
            ORDER BY total_thinking_chars DESC"""
        ).fetchall()

        total_sessions = con.execute(
            "SELECT COUNT(*) AS n FROM sessions"
        ).fetchone()["n"]

        total_chars = sum(r["total_thinking_chars"] for r in rows)
        n = len(rows)

        return {
            "sessions_with_thinking": n,
            "total_sessions": total_sessions,
            "avg_thinking_chars": total_chars / n if n > 0 else 0,
            "by_session": [
                {
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


def get_permission_mode_breakdown(db_path: Path) -> list[dict]:
    """Permission mode distribution across sessions.

    Returns:
        [{mode, count, pct}, ...] sorted by count desc.
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT permission_mode, COUNT(*) AS count
            FROM sessions
            WHERE permission_mode IS NOT NULL
            GROUP BY permission_mode
            ORDER BY count DESC"""
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


def get_error_breakdown(db_path: Path) -> list[dict]:
    """Categorized error breakdown across all sessions.

    Returns:
        [{category, count, pct}, ...] sorted by count desc.
        Empty list if no errors.
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT tool_name, command
            FROM tool_calls
            WHERE is_error = 1"""
        ).fetchall()

        if not rows:
            return []

        categories: dict[str, int] = {}
        for r in rows:
            cat = _categorize_error(r["tool_name"], r["command"])
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
    db_path: Path, session_id: str,
) -> list[dict]:
    """Categorized error breakdown for a single session.

    Returns:
        [{category, count}, ...] sorted by count desc.
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT tool_name, command
            FROM tool_calls
            WHERE is_error = 1 AND session_id = ?""",
            (session_id,),
        ).fetchall()

        if not rows:
            return []

        categories: dict[str, int] = {}
        for r in rows:
            cat = _categorize_error(r["tool_name"], r["command"])
            categories[cat] = categories.get(cat, 0) + 1

        result = [
            {"category": cat, "count": count}
            for cat, count in categories.items()
        ]
        result.sort(key=lambda x: x["count"], reverse=True)
        return result
    finally:
        con.close()
