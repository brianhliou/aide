"""Tests for action-summary artifact proposals."""

from datetime import datetime, timedelta, timezone

from aide.actions import build_action_proposals, save_action_proposals
from aide.artifacts import get_artifact
from aide.db import ingest_sessions, init_db
from aide.models import ParsedMessage, ParsedSession, ToolCall


def _no_edit_session() -> ParsedSession:
    now = datetime.now(timezone.utc) - timedelta(hours=1)
    calls = [
        ToolCall(
            tool_name="Read",
            file_path=f"src/file_{index}.py",
            timestamp=now,
        )
        for index in range(20)
    ]
    message = ParsedMessage(
        uuid="msg-action",
        parent_uuid=None,
        session_id="action-1",
        timestamp=now,
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
        provider="codex",
        session_id="action-1",
        project_path="/Users/test/projects/aide",
        project_name="aide",
        source_file="/fake/action-1.jsonl",
        started_at=now,
        ended_at=now + timedelta(minutes=20),
        messages=[message],
        duration_seconds=1200,
        total_input_tokens=100,
        total_output_tokens=50,
        total_cache_read_tokens=0,
        total_cache_creation_tokens=0,
        estimated_cost_usd=0.25,
        message_count=1,
        user_message_count=0,
        assistant_message_count=1,
        tool_call_count=len(calls),
        file_read_count=len(calls),
        file_write_count=0,
        file_edit_count=0,
        bash_count=0,
        active_duration_seconds=1200,
    )


def _db(tmp_path):
    db_path = tmp_path / "actions.db"
    init_db(db_path)
    ingest_sessions(db_path, [_no_edit_session()])
    return db_path


def test_build_action_proposals_groups_signal_by_project(tmp_path):
    db_path = _db(tmp_path)

    result = build_action_proposals(db_path, "no-edits")

    assert result.signal_label == "no edits"
    assert len(result.proposals) == 1
    proposal = result.proposals[0]
    assert proposal.artifact.artifact_type == "planner_signal"
    assert proposal.artifact.project_name == "aide"
    assert proposal.evidence[0].evidence_kind == "investigation_flag"
    assert "score" in proposal.evidence[0].summary


def test_save_action_proposals_persists_artifact_and_evidence(tmp_path):
    db_path = _db(tmp_path)
    result = build_action_proposals(db_path, "no-edits")

    ids = save_action_proposals(db_path, result)

    stored = get_artifact(db_path, ids[0])
    assert stored["title"] == "Split research-only work from implementation"
    assert stored["status"] == "proposed"
    assert stored["evidence"][0]["session_id"] == "action-1"


def test_build_action_proposals_skips_existing_artifact(tmp_path):
    db_path = _db(tmp_path)
    first = build_action_proposals(db_path, "no-edits")
    save_action_proposals(db_path, first)

    second = build_action_proposals(db_path, "no-edits")

    assert second.proposals == []
    assert second.skipped_existing == 1
