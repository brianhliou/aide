"""Tests for persisted effectiveness snapshots."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from aide.db import ingest_sessions, init_db
from aide.effectiveness import list_effectiveness_snapshots, snapshot_effectiveness
from aide.models import ParsedMessage, ParsedSession, ToolCall, WorkBlock


def _session(
    session_id: str,
    *,
    provider: str,
    project_name: str,
    cost: float,
    days_ago: int,
    edit: bool = True,
) -> ParsedSession:
    started = datetime.now(timezone.utc) - timedelta(days=days_ago)
    ended = started + timedelta(minutes=20)
    calls = [
        ToolCall(tool_name="Read", file_path="src/main.py", timestamp=started),
    ]
    if edit:
        calls.append(ToolCall(tool_name="Edit", file_path="src/main.py", timestamp=started))
    else:
        calls.extend(
            ToolCall(
                tool_name="Read",
                file_path=f"src/file_{index}.py",
                timestamp=started,
            )
            for index in range(20)
        )

    message = ParsedMessage(
        uuid=f"msg-{session_id}",
        parent_uuid=None,
        session_id=session_id,
        timestamp=started,
        role="assistant",
        type="assistant",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        content_length=100,
        tool_calls=calls,
    )
    return ParsedSession(
        provider=provider,
        session_id=session_id,
        project_path=f"/Users/test/projects/{project_name}",
        project_name=project_name,
        source_file=f"/fake/{session_id}.jsonl",
        started_at=started,
        ended_at=ended,
        messages=[message],
        duration_seconds=1200,
        total_input_tokens=100,
        total_output_tokens=50,
        total_cache_read_tokens=0,
        total_cache_creation_tokens=0,
        estimated_cost_usd=cost,
        message_count=1,
        user_message_count=1,
        assistant_message_count=1,
        tool_call_count=len(calls),
        file_read_count=len(calls) - (1 if edit else 0),
        file_write_count=0,
        file_edit_count=1 if edit else 0,
        bash_count=0,
        active_duration_seconds=1200,
        work_blocks=[
            WorkBlock(
                session_id=session_id,
                block_index=0,
                started_at=started,
                ended_at=ended,
                duration_seconds=1200,
                message_count=1,
            ),
        ],
    )


def test_snapshot_effectiveness_persists_all_provider_and_project_rows(tmp_path):
    db_path = tmp_path / "aide.db"
    init_db(db_path)
    ingest_sessions(
        db_path,
        [
            _session(
                "claude-alpha",
                provider="claude",
                project_name="alpha",
                cost=2.0,
                days_ago=1,
            ),
            _session(
                "codex-beta",
                provider="codex",
                project_name="beta",
                cost=4.0,
                days_ago=1,
                edit=False,
            ),
            _session(
                "old-alpha",
                provider="claude",
                project_name="alpha",
                cost=8.0,
                days_ago=45,
            ),
        ],
    )

    rows = snapshot_effectiveness(
        db_path,
        snapshot_date=datetime(2026, 5, 4, tzinfo=timezone.utc).date(),
        window_days=30,
    )

    assert [(row.scope, row.provider, row.project_name) for row in rows] == [
        ("all", "__all__", "__all__"),
        ("provider", "claude", "__all__"),
        ("provider", "codex", "__all__"),
        ("project", "codex", "beta"),
        ("project", "claude", "alpha"),
    ]
    all_row = rows[0]
    assert all_row.metrics["session_count"] == 2
    assert all_row.metrics["total_cost"] == 6.0
    assert all_row.metrics["no_edit_session_count"] == 1

    stored = list_effectiveness_snapshots(db_path, limit=10)
    assert len(stored) == 5
    assert stored[0]["snapshot_date"] == "2026-05-04"


def test_snapshot_effectiveness_is_idempotent_for_same_date_and_window(tmp_path):
    db_path = tmp_path / "aide.db"
    init_db(db_path)
    ingest_sessions(
        db_path,
        [
            _session(
                "claude-alpha",
                provider="claude",
                project_name="alpha",
                cost=2.0,
                days_ago=1,
            )
        ],
    )
    day = datetime(2026, 5, 4, tzinfo=timezone.utc).date()

    snapshot_effectiveness(db_path, snapshot_date=day, window_days=30)
    snapshot_effectiveness(db_path, snapshot_date=day, window_days=30)

    con = sqlite3.connect(db_path)
    try:
        count = con.execute("SELECT COUNT(*) FROM effectiveness_snapshots").fetchone()[0]
    finally:
        con.close()

    assert count == 3
