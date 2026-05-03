"""Tests for heuristic session digest proposals."""

from datetime import datetime, timedelta, timezone

from aide.artifacts import get_artifact
from aide.db import ingest_sessions, init_db
from aide.digest import build_digest, save_digest_proposals
from aide.models import ParsedMessage, ParsedSession, ToolCall


def _now() -> datetime:
    return datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def _session(
    tool_calls: list[ToolCall],
    *,
    session_id: str = "digest-1",
    provider: str = "codex",
    project_name: str = "aide",
    project_path: str = "/Users/test/projects/aide",
    cost: float = 0.25,
    active_duration_seconds: int = 600,
    duration_seconds: int = 900,
    file_edit_count: int = 0,
    file_write_count: int = 0,
) -> ParsedSession:
    now = _now()
    msg = ParsedMessage(
        uuid="msg-001",
        parent_uuid=None,
        session_id=session_id,
        timestamp=now,
        role="assistant",
        type="assistant",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        content_length=100,
        tool_calls=tool_calls,
    )
    return ParsedSession(
        provider=provider,
        session_id=session_id,
        project_path=project_path,
        project_name=project_name,
        source_file=f"/fake/{session_id}.jsonl",
        started_at=now,
        ended_at=now + timedelta(seconds=duration_seconds),
        messages=[msg],
        duration_seconds=duration_seconds,
        total_input_tokens=100,
        total_output_tokens=50,
        total_cache_read_tokens=0,
        total_cache_creation_tokens=0,
        estimated_cost_usd=cost,
        message_count=1,
        user_message_count=0,
        assistant_message_count=1,
        tool_call_count=len(tool_calls),
        file_read_count=sum(1 for tc in tool_calls if tc.tool_name == "Read"),
        file_write_count=file_write_count,
        file_edit_count=file_edit_count,
        bash_count=sum(1 for tc in tool_calls if tc.tool_name == "Bash"),
        tool_error_count=sum(1 for tc in tool_calls if tc.is_error),
        active_duration_seconds=active_duration_seconds,
    )


def _db_with_session(tmp_path, session: ParsedSession):
    db_path = tmp_path / "digest.db"
    init_db(db_path)
    ingest_sessions(db_path, [session])
    return db_path


def test_build_digest_returns_none_for_missing_session(tmp_path):
    db_path = tmp_path / "empty.db"
    init_db(db_path)

    assert build_digest(db_path, "missing") is None


def test_build_digest_proposes_verification_recipe(tmp_path):
    now = _now()
    db_path = _db_with_session(
        tmp_path,
        _session([
            ToolCall(
                tool_name="Bash",
                file_path=None,
                timestamp=now,
                command="uv run pytest tests/test_digest.py",
            )
        ]),
    )

    result = build_digest(db_path, "digest-1", provider="codex")

    assert result is not None
    assert result.proposals[0].artifact.artifact_type == "verification_recipe"
    assert result.proposals[0].artifact.title == "Verify with `uv run pytest`"
    assert result.proposals[0].evidence[0].evidence_kind == "verification_result"


def test_build_digest_proposes_permission_risk(tmp_path):
    now = _now()
    db_path = _db_with_session(
        tmp_path,
        _session([
            ToolCall(
                tool_name="Bash",
                file_path=None,
                timestamp=now,
                command="uv run pytest",
                description="sandbox_permissions=require_escalated",
            ),
            ToolCall(
                tool_name="Bash",
                file_path=None,
                timestamp=now,
                command="open http://localhost",
                description="sandbox denied",
                is_error=True,
            ),
        ]),
    )

    result = build_digest(db_path, "digest-1", provider="codex")
    types = {proposal.artifact.artifact_type for proposal in result.proposals}

    assert "risky_action" in types


def test_build_digest_proposes_agent_mistake_for_file_targeting(tmp_path):
    now = _now()
    db_path = _db_with_session(
        tmp_path,
        _session([
            ToolCall(tool_name="Edit", file_path=None, timestamp=now, is_error=True),
            ToolCall(tool_name="Edit", file_path=None, timestamp=now, is_error=True),
        ]),
    )

    result = build_digest(db_path, "digest-1", provider="codex")
    mistake = next(
        proposal
        for proposal in result.proposals
        if proposal.artifact.artifact_type == "agent_mistake"
    )

    assert "file targeting" in mistake.reason
    assert mistake.evidence[0].evidence_kind == "error_pattern"


def test_build_digest_proposes_planner_signal_for_expensive_no_edit(tmp_path):
    now = _now()
    db_path = _db_with_session(
        tmp_path,
        _session(
            [
                ToolCall(tool_name="Read", file_path=f"src/{index}.py", timestamp=now)
                for index in range(12)
            ],
            cost=2.50,
        ),
    )

    result = build_digest(db_path, "digest-1", provider="codex")
    types = {proposal.artifact.artifact_type for proposal in result.proposals}

    assert "planner_signal" in types


def test_build_digest_proposes_future_instruction_for_weak_project(tmp_path):
    now = _now()
    db_path = _db_with_session(
        tmp_path,
        _session(
            [ToolCall(tool_name="Read", file_path="src/app.py", timestamp=now)],
            project_name="codex",
            project_path="/Users/test/.codex",
        ),
    )

    result = build_digest(db_path, "digest-1", provider="codex")
    types = {proposal.artifact.artifact_type for proposal in result.proposals}

    assert "future_agent_instruction" in types


def test_save_digest_proposals_persists_artifacts_and_evidence(tmp_path):
    now = _now()
    db_path = _db_with_session(
        tmp_path,
        _session([
            ToolCall(
                tool_name="Bash",
                file_path=None,
                timestamp=now,
                command="just check",
            )
        ]),
    )
    result = build_digest(db_path, "digest-1", provider="codex")

    ids = save_digest_proposals(db_path, result)
    stored = get_artifact(db_path, ids[0])

    assert len(ids) == 1
    assert stored["artifact_type"] == "verification_recipe"
    assert stored["events"][0]["note"] == "successful verification command"
