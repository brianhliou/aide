"""Database query functions for session autopsy analysis."""

from __future__ import annotations

from pathlib import Path

from aide.db import get_connection


def get_session(db_path: Path, session_id: str) -> dict | None:
    """Fetch a single session by session_id.

    Returns dict of session columns or None if not found.
    """
    con = get_connection(db_path)
    try:
        row = con.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        con.close()


def get_session_messages(db_path: Path, session_id: str) -> list[dict]:
    """Fetch all messages for a session, ordered by timestamp."""
    con = get_connection(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_session_tool_calls(db_path: Path, session_id: str) -> list[dict]:
    """Fetch all tool calls for a session, ordered by timestamp."""
    con = get_connection(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM tool_calls WHERE session_id = ? ORDER BY timestamp",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_session_tool_usage(db_path: Path, session_id: str) -> list[dict]:
    """Aggregate tool call counts for a session, sorted by count descending."""
    con = get_connection(db_path)
    try:
        rows = con.execute(
            "SELECT tool_name, COUNT(*) as count FROM tool_calls "
            "WHERE session_id = ? GROUP BY tool_name ORDER BY count DESC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_session_files_touched(db_path: Path, session_id: str) -> list[dict]:
    """Aggregate file access patterns for a session.

    Categorizes tool calls by type:
    - Read tools: Read, Glob, Grep
    - Edit tools: Edit
    - Write tools: Write

    Returns list of dicts with file_path, read_count, edit_count, write_count, total.
    Only includes rows where file_path IS NOT NULL. Sorted by total desc.
    """
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT
                file_path,
                SUM(CASE WHEN tool_name IN ('Read', 'Glob', 'Grep')
                    THEN 1 ELSE 0 END) AS read_count,
                SUM(CASE WHEN tool_name = 'Edit' THEN 1 ELSE 0 END) AS edit_count,
                SUM(CASE WHEN tool_name = 'Write' THEN 1 ELSE 0 END) AS write_count,
                COUNT(*) AS total
            FROM tool_calls
            WHERE session_id = ? AND file_path IS NOT NULL
            GROUP BY file_path
            ORDER BY total DESC""",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()
