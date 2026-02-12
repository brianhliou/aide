"""Tests for the SQLite database layer."""

from datetime import datetime, timezone

from aide.db import (
    get_ingested_file,
    get_summary_stats,
    ingest_sessions,
    init_db,
    log_ingestion,
    rebuild_daily_stats,
)
from aide.models import ParsedMessage, ParsedSession, ToolCall


def _make_session(
    session_id="sess-001",
    project_path="-Users-brian-projects-myapp",
    project_name="myapp",
    source_file="/logs/myapp.jsonl",
    started_at=None,
    ended_at=None,
    input_tokens=1000,
    output_tokens=500,
    cache_read_tokens=200,
    cache_creation_tokens=50,
    cost=0.05,
    messages=None,
):
    """Helper to build a ParsedSession with sensible defaults."""
    if started_at is None:
        started_at = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    if ended_at is None:
        ended_at = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    if messages is None:
        messages = [
            ParsedMessage(
                uuid="msg-001",
                parent_uuid=None,
                session_id=session_id,
                timestamp=started_at,
                role="user",
                type="user",
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                content_length=42,
            ),
            ParsedMessage(
                uuid="msg-002",
                parent_uuid="msg-001",
                session_id=session_id,
                timestamp=ended_at,
                role="assistant",
                type="assistant",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
                content_length=256,
                tool_calls=[
                    ToolCall(
                        tool_name="Read",
                        file_path="/src/main.py",
                        timestamp=ended_at,
                    ),
                    ToolCall(
                        tool_name="Edit",
                        file_path="/src/main.py",
                        timestamp=ended_at,
                    ),
                ],
            ),
        ]

    return ParsedSession(
        session_id=session_id,
        project_path=project_path,
        project_name=project_name,
        source_file=source_file,
        started_at=started_at,
        ended_at=ended_at,
        messages=messages,
        duration_seconds=1800,
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_cache_read_tokens=cache_read_tokens,
        total_cache_creation_tokens=cache_creation_tokens,
        estimated_cost_usd=cost,
        message_count=len(messages),
        user_message_count=1,
        assistant_message_count=1,
        tool_call_count=2,
        file_read_count=1,
        file_write_count=0,
        file_edit_count=1,
        bash_count=0,
        compaction_count=0,
        peak_context_tokens=0,
    )


def test_init_db_creates_tables(tmp_db):
    """init_db creates all 5 expected tables."""
    import sqlite3

    init_db(tmp_db)
    con = sqlite3.connect(tmp_db)
    tables = {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    con.close()

    expected = {"sessions", "messages", "tool_calls", "daily_stats", "ingest_log"}
    assert expected.issubset(tables)


def test_init_db_is_idempotent(tmp_db):
    """Calling init_db twice does not raise."""
    init_db(tmp_db)
    init_db(tmp_db)  # should not raise


def test_ingest_sessions(tmp_db):
    """Ingesting sessions stores session, messages, and tool_calls."""
    import sqlite3

    init_db(tmp_db)
    session = _make_session()
    count = ingest_sessions(tmp_db, [session])

    assert count == 1

    con = sqlite3.connect(tmp_db)
    con.row_factory = sqlite3.Row

    # Check session row
    row = con.execute(
        "SELECT * FROM sessions WHERE session_id = ?", ("sess-001",)
    ).fetchone()
    assert row is not None
    assert row["project_name"] == "myapp"
    assert row["total_input_tokens"] == 1000
    assert row["tool_call_count"] == 2

    # Check messages
    msgs = con.execute(
        "SELECT * FROM messages WHERE session_id = ?", ("sess-001",)
    ).fetchall()
    assert len(msgs) == 2

    # Check tool_calls
    tcs = con.execute(
        "SELECT * FROM tool_calls WHERE session_id = ?", ("sess-001",)
    ).fetchall()
    assert len(tcs) == 2
    tool_names = {r["tool_name"] for r in tcs}
    assert tool_names == {"Read", "Edit"}

    con.close()


def test_reingest_replaces_not_duplicates(tmp_db):
    """Re-ingesting the same session_id replaces data, not duplicates it."""
    import sqlite3

    init_db(tmp_db)

    session_v1 = _make_session(cost=0.05)
    ingest_sessions(tmp_db, [session_v1])

    # Re-ingest with updated cost
    session_v2 = _make_session(cost=0.10)
    ingest_sessions(tmp_db, [session_v2])

    con = sqlite3.connect(tmp_db)
    con.row_factory = sqlite3.Row

    # Should still be 1 session
    count = con.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
    assert count == 1

    # Cost should be updated
    row = con.execute(
        "SELECT estimated_cost_usd FROM sessions WHERE session_id = ?",
        ("sess-001",),
    ).fetchone()
    assert row["estimated_cost_usd"] == 0.10

    # Messages and tool_calls should not be duplicated
    msg_count = con.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    assert msg_count == 2  # not 4

    tc_count = con.execute("SELECT COUNT(*) AS c FROM tool_calls").fetchone()["c"]
    assert tc_count == 2  # not 4

    con.close()


def test_rebuild_daily_stats(tmp_db):
    """rebuild_daily_stats produces correct per-project and aggregate rows."""
    import sqlite3

    init_db(tmp_db)

    # Two sessions on the same day, different projects
    s1 = _make_session(
        session_id="sess-001",
        project_name="alpha",
        cost=0.10,
        input_tokens=1000,
        output_tokens=500,
    )
    s2 = _make_session(
        session_id="sess-002",
        project_name="beta",
        source_file="/logs/beta.jsonl",
        cost=0.20,
        input_tokens=2000,
        output_tokens=800,
    )
    ingest_sessions(tmp_db, [s1, s2])
    rebuild_daily_stats(tmp_db)

    con = sqlite3.connect(tmp_db)
    con.row_factory = sqlite3.Row

    rows = con.execute("SELECT * FROM daily_stats ORDER BY project_name").fetchall()
    # Expect 3 rows: NULL aggregate, alpha, beta
    assert len(rows) == 3

    # NULL aggregate row
    agg = [r for r in rows if r["project_name"] is None]
    assert len(agg) == 1
    assert agg[0]["session_count"] == 2
    assert abs(agg[0]["estimated_cost_usd"] - 0.30) < 1e-9
    assert agg[0]["total_input_tokens"] == 3000

    # Per-project rows
    alpha = [r for r in rows if r["project_name"] == "alpha"]
    assert len(alpha) == 1
    assert alpha[0]["session_count"] == 1
    assert abs(alpha[0]["estimated_cost_usd"] - 0.10) < 1e-9

    beta = [r for r in rows if r["project_name"] == "beta"]
    assert len(beta) == 1
    assert abs(beta[0]["estimated_cost_usd"] - 0.20) < 1e-9

    con.close()


def test_rebuild_daily_stats_multiple_days(tmp_db):
    """Daily stats groups correctly across multiple days."""
    init_db(tmp_db)

    s1 = _make_session(
        session_id="sess-d1",
        started_at=datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2025, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
        cost=0.10,
    )
    s2 = _make_session(
        session_id="sess-d2",
        started_at=datetime(2025, 1, 16, 10, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2025, 1, 16, 11, 0, 0, tzinfo=timezone.utc),
        cost=0.20,
    )
    ingest_sessions(tmp_db, [s1, s2])
    rebuild_daily_stats(tmp_db)

    import sqlite3

    con = sqlite3.connect(tmp_db)
    con.row_factory = sqlite3.Row

    dates = con.execute(
        "SELECT DISTINCT date FROM daily_stats ORDER BY date"
    ).fetchall()
    assert len(dates) == 2
    assert dates[0]["date"] == "2025-01-15"
    assert dates[1]["date"] == "2025-01-16"

    con.close()


def test_get_summary_stats(tmp_db):
    """get_summary_stats returns correct totals."""
    init_db(tmp_db)

    s1 = _make_session(
        session_id="sess-001",
        project_name="alpha",
        cost=0.10,
    )
    s2 = _make_session(
        session_id="sess-002",
        project_name="beta",
        source_file="/logs/beta.jsonl",
        cost=0.25,
    )
    ingest_sessions(tmp_db, [s1, s2])

    stats = get_summary_stats(tmp_db)
    assert stats["total_sessions"] == 2
    assert abs(stats["total_cost"] - 0.35) < 1e-9
    assert stats["total_projects"] == 2
    assert stats["date_range"]["min"] is not None
    assert stats["date_range"]["max"] is not None

    by_project = {p["project_name"]: p for p in stats["sessions_by_project"]}
    assert "alpha" in by_project
    assert "beta" in by_project
    assert by_project["alpha"]["session_count"] == 1
    assert abs(by_project["beta"]["total_cost"] - 0.25) < 1e-9


def test_get_summary_stats_empty(tmp_db):
    """get_summary_stats on empty db returns zeros."""
    init_db(tmp_db)
    stats = get_summary_stats(tmp_db)
    assert stats["total_sessions"] == 0
    assert stats["total_cost"] == 0
    assert stats["total_projects"] == 0
    assert stats["date_range"]["min"] is None
    assert stats["sessions_by_project"] == []


def test_ingest_sessions_stores_compaction_fields(tmp_db):
    """Ingesting sessions stores compaction_count and peak_context_tokens."""
    import sqlite3

    init_db(tmp_db)
    session = _make_session()
    session.compaction_count = 2
    session.peak_context_tokens = 180000
    ingest_sessions(tmp_db, [session])

    con = sqlite3.connect(tmp_db)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT compaction_count, peak_context_tokens FROM sessions WHERE session_id = ?",
        ("sess-001",),
    ).fetchone()
    con.close()

    assert row["compaction_count"] == 2
    assert row["peak_context_tokens"] == 180000


def test_ingest_sessions_compaction_defaults_to_zero(tmp_db):
    """Default compaction values are 0 when not explicitly set."""
    import sqlite3

    init_db(tmp_db)
    session = _make_session()
    ingest_sessions(tmp_db, [session])

    con = sqlite3.connect(tmp_db)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT compaction_count, peak_context_tokens FROM sessions WHERE session_id = ?",
        ("sess-001",),
    ).fetchone()
    con.close()

    assert row["compaction_count"] == 0
    assert row["peak_context_tokens"] == 0


def test_migrate_db_adds_missing_columns(tmp_db):
    """Migration adds compaction columns to an old schema without them."""
    import sqlite3

    # Create old schema without compaction columns
    con = sqlite3.connect(tmp_db)
    con.execute("""CREATE TABLE IF NOT EXISTS sessions (
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
        source_file TEXT NOT NULL,
        ingested_at TEXT DEFAULT (datetime('now'))
    )""")
    con.commit()
    con.close()

    from aide.db import _migrate_db

    _migrate_db(tmp_db)

    con = sqlite3.connect(tmp_db)
    cols = {row[1] for row in con.execute("PRAGMA table_info(sessions)").fetchall()}
    con.close()

    assert "compaction_count" in cols
    assert "peak_context_tokens" in cols


def test_log_ingestion_and_get_ingested_file(tmp_db):
    """log_ingestion records a file; get_ingested_file retrieves it."""
    init_db(tmp_db)

    log_ingestion(tmp_db, "/logs/a.jsonl", file_size=1024, file_mtime=1700000000.0, session_count=3)

    result = get_ingested_file(tmp_db, "/logs/a.jsonl")
    assert result is not None
    assert result["source_file"] == "/logs/a.jsonl"
    assert result["file_size"] == 1024
    assert result["file_mtime"] == 1700000000.0


def test_get_ingested_file_not_found(tmp_db):
    """get_ingested_file returns None for unknown file."""
    init_db(tmp_db)
    assert get_ingested_file(tmp_db, "/no/such/file.jsonl") is None


def test_log_ingestion_replace(tmp_db):
    """Re-logging same source_file replaces the previous entry."""
    import sqlite3

    init_db(tmp_db)

    log_ingestion(tmp_db, "/logs/a.jsonl", file_size=1024, file_mtime=1700000000.0, session_count=3)
    log_ingestion(tmp_db, "/logs/a.jsonl", file_size=2048, file_mtime=1700001000.0, session_count=5)

    result = get_ingested_file(tmp_db, "/logs/a.jsonl")
    assert result["file_size"] == 2048
    assert result["file_mtime"] == 1700001000.0

    # Only one row
    con = sqlite3.connect(tmp_db)
    count = con.execute("SELECT COUNT(*) FROM ingest_log").fetchone()[0]
    con.close()
    assert count == 1
