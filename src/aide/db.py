"""SQLite database layer — stores parsed session data and provides query functions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from aide.models import ParsedSession

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    project_path TEXT NOT NULL,
    project_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds INTEGER,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_cache_read_tokens INTEGER DEFAULT 0,
    total_cache_creation_tokens INTEGER DEFAULT 0,
    estimated_cost_usd REAL,
    message_count INTEGER DEFAULT 0,
    user_message_count INTEGER DEFAULT 0,
    assistant_message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    file_read_count INTEGER DEFAULT 0,
    file_write_count INTEGER DEFAULT 0,
    file_edit_count INTEGER DEFAULT 0,
    bash_count INTEGER DEFAULT 0,
    compaction_count INTEGER DEFAULT 0,
    peak_context_tokens INTEGER DEFAULT 0,
    source_file TEXT NOT NULL,
    ingested_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    message_uuid TEXT NOT NULL,
    parent_uuid TEXT,
    role TEXT NOT NULL,
    type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    content_length INTEGER DEFAULT 0,
    has_tool_use INTEGER DEFAULT 0,
    tool_names TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    message_uuid TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    file_path TEXT,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT NOT NULL,
    project_name TEXT,
    session_count INTEGER DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_cache_read_tokens INTEGER DEFAULT 0,
    estimated_cost_usd REAL DEFAULT 0,
    total_duration_seconds INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    PRIMARY KEY (date, project_name)
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL UNIQUE,
    file_size INTEGER,
    file_mtime REAL,
    session_count INTEGER,
    ingested_at TEXT DEFAULT (datetime('now'))
);
"""


def init_db(db_path: Path) -> None:
    """Create tables if they don't exist, then run any needed migrations."""
    con = sqlite3.connect(db_path)
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()
    _migrate_db(db_path)


def _migrate_db(db_path: Path) -> None:
    """Add columns that may be missing from older databases."""
    con = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(sessions)").fetchall()}
        if "compaction_count" not in cols:
            con.execute("ALTER TABLE sessions ADD COLUMN compaction_count INTEGER DEFAULT 0")
        if "peak_context_tokens" not in cols:
            con.execute("ALTER TABLE sessions ADD COLUMN peak_context_tokens INTEGER DEFAULT 0")
        con.commit()
    finally:
        con.close()


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Get a connection with row_factory = sqlite3.Row."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def ingest_sessions(db_path: Path, sessions: list[ParsedSession]) -> int:
    """Insert parsed sessions into the database.

    Uses INSERT OR REPLACE on session_id. Also inserts messages and tool_calls.
    Returns count of sessions ingested.
    """
    con = get_connection(db_path)
    try:
        for s in sessions:
            # Delete existing child rows for re-ingestion
            con.execute("DELETE FROM messages WHERE session_id = ?", (s.session_id,))
            con.execute(
                "DELETE FROM tool_calls WHERE session_id = ?", (s.session_id,)
            )

            # Upsert session
            con.execute(
                """INSERT OR REPLACE INTO sessions (
                    session_id, project_path, project_name, started_at, ended_at,
                    duration_seconds, total_input_tokens, total_output_tokens,
                    total_cache_read_tokens, total_cache_creation_tokens,
                    estimated_cost_usd, message_count, user_message_count,
                    assistant_message_count, tool_call_count, file_read_count,
                    file_write_count, file_edit_count, bash_count,
                    compaction_count, peak_context_tokens, source_file
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    s.session_id,
                    s.project_path,
                    s.project_name,
                    s.started_at.isoformat(),
                    s.ended_at.isoformat() if s.ended_at else None,
                    s.duration_seconds,
                    s.total_input_tokens,
                    s.total_output_tokens,
                    s.total_cache_read_tokens,
                    s.total_cache_creation_tokens,
                    s.estimated_cost_usd,
                    s.message_count,
                    s.user_message_count,
                    s.assistant_message_count,
                    s.tool_call_count,
                    s.file_read_count,
                    s.file_write_count,
                    s.file_edit_count,
                    s.bash_count,
                    s.compaction_count,
                    s.peak_context_tokens,
                    s.source_file,
                ),
            )

            # Insert messages
            for m in s.messages:
                tool_names = (
                    ",".join(tc.tool_name for tc in m.tool_calls)
                    if m.tool_calls
                    else None
                )
                con.execute(
                    """INSERT INTO messages (
                        session_id, message_uuid, parent_uuid, role, type,
                        timestamp, input_tokens, output_tokens,
                        cache_read_tokens, cache_creation_tokens,
                        content_length, has_tool_use, tool_names
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        s.session_id,
                        m.uuid,
                        m.parent_uuid,
                        m.role,
                        m.type,
                        m.timestamp.isoformat(),
                        m.input_tokens,
                        m.output_tokens,
                        m.cache_read_tokens,
                        m.cache_creation_tokens,
                        m.content_length,
                        1 if m.tool_calls else 0,
                        tool_names,
                    ),
                )

                # Insert tool_calls
                for tc in m.tool_calls:
                    con.execute(
                        """INSERT INTO tool_calls (
                            session_id, message_uuid, tool_name, file_path, timestamp
                        ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            s.session_id,
                            m.uuid,
                            tc.tool_name,
                            tc.file_path,
                            tc.timestamp.isoformat(),
                        ),
                    )

        con.commit()
        return len(sessions)
    finally:
        con.close()


def rebuild_daily_stats(db_path: Path) -> None:
    """Delete and rebuild the daily_stats table from sessions data.

    Groups by date (from started_at) and project_name.
    Also creates aggregate rows per date with project_name = NULL.
    """
    con = get_connection(db_path)
    try:
        con.execute("DELETE FROM daily_stats")

        # Per-project stats
        con.execute(
            """INSERT INTO daily_stats (
                date, project_name, session_count,
                total_input_tokens, total_output_tokens,
                total_cache_read_tokens, estimated_cost_usd,
                total_duration_seconds, tool_call_count
            )
            SELECT
                date(started_at) AS date,
                project_name,
                COUNT(*) AS session_count,
                SUM(total_input_tokens) AS total_input_tokens,
                SUM(total_output_tokens) AS total_output_tokens,
                SUM(total_cache_read_tokens) AS total_cache_read_tokens,
                SUM(estimated_cost_usd) AS estimated_cost_usd,
                SUM(duration_seconds) AS total_duration_seconds,
                SUM(tool_call_count) AS tool_call_count
            FROM sessions
            GROUP BY date(started_at), project_name"""
        )

        # All-projects aggregate per date (project_name = NULL)
        con.execute(
            """INSERT INTO daily_stats (
                date, project_name, session_count,
                total_input_tokens, total_output_tokens,
                total_cache_read_tokens, estimated_cost_usd,
                total_duration_seconds, tool_call_count
            )
            SELECT
                date(started_at) AS date,
                NULL,
                COUNT(*) AS session_count,
                SUM(total_input_tokens) AS total_input_tokens,
                SUM(total_output_tokens) AS total_output_tokens,
                SUM(total_cache_read_tokens) AS total_cache_read_tokens,
                SUM(estimated_cost_usd) AS estimated_cost_usd,
                SUM(duration_seconds) AS total_duration_seconds,
                SUM(tool_call_count) AS tool_call_count
            FROM sessions
            GROUP BY date(started_at)"""
        )

        con.commit()
    finally:
        con.close()


def log_ingestion(
    db_path: Path,
    source_file: str,
    file_size: int,
    file_mtime: float,
    session_count: int,
) -> None:
    """Record a file in the ingest_log. INSERT OR REPLACE on source_file."""
    con = get_connection(db_path)
    try:
        con.execute(
            """INSERT OR REPLACE INTO ingest_log
                (source_file, file_size, file_mtime, session_count)
            VALUES (?, ?, ?, ?)""",
            (source_file, file_size, file_mtime, session_count),
        )
        con.commit()
    finally:
        con.close()


def get_ingested_file(db_path: Path, source_file: str) -> dict | None:
    """Check if a file has been ingested.

    Returns {source_file, file_size, file_mtime} or None.
    """
    con = get_connection(db_path)
    try:
        row = con.execute(
            "SELECT source_file, file_size, file_mtime FROM ingest_log WHERE source_file = ?",
            (source_file,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        con.close()


def get_summary_stats(db_path: Path) -> dict:
    """Return summary statistics across all sessions.

    Returns: total_sessions, total_cost, total_projects, date_range,
    sessions_by_project.
    """
    con = get_connection(db_path)
    try:
        row = con.execute(
            """SELECT
                COUNT(*) AS total_sessions,
                COALESCE(SUM(estimated_cost_usd), 0) AS total_cost,
                COUNT(DISTINCT project_name) AS total_projects,
                MIN(started_at) AS min_date,
                MAX(started_at) AS max_date
            FROM sessions"""
        ).fetchone()

        by_project = con.execute(
            """SELECT
                project_name,
                COUNT(*) AS session_count,
                COALESCE(SUM(estimated_cost_usd), 0) AS total_cost
            FROM sessions
            GROUP BY project_name
            ORDER BY total_cost DESC"""
        ).fetchall()

        return {
            "total_sessions": row["total_sessions"],
            "total_cost": row["total_cost"],
            "total_projects": row["total_projects"],
            "date_range": {
                "min": row["min_date"],
                "max": row["max_date"],
            },
            "sessions_by_project": [
                {
                    "project_name": r["project_name"],
                    "session_count": r["session_count"],
                    "total_cost": r["total_cost"],
                }
                for r in by_project
            ],
        }
    finally:
        con.close()
