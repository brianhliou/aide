"""Tests for semantic artifact persistence helpers."""

from datetime import datetime, timezone

import pytest

from aide.artifacts import (
    accept_artifact,
    add_artifact_evidence,
    get_artifact,
    list_artifacts,
    propose_artifact,
    reject_artifact,
)
from aide.db import ingest_sessions, init_db
from aide.models import ArtifactEvidence, ParsedMessage, ParsedSession, SemanticArtifact


def _now() -> datetime:
    return datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def _make_session(
    session_id: str = "sess-001",
    provider: str = "codex",
    project_name: str = "aide",
) -> ParsedSession:
    now = _now()
    message = ParsedMessage(
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
    )
    return ParsedSession(
        provider=provider,
        session_id=session_id,
        project_path=f"/Users/test/projects/{project_name}",
        project_name=project_name,
        source_file=f"/fake/{session_id}.jsonl",
        started_at=now,
        ended_at=now,
        messages=[message],
        duration_seconds=0,
        total_input_tokens=100,
        total_output_tokens=50,
        total_cache_read_tokens=0,
        total_cache_creation_tokens=0,
        estimated_cost_usd=0.01,
        message_count=1,
        user_message_count=0,
        assistant_message_count=1,
        tool_call_count=0,
        file_read_count=0,
        file_write_count=0,
        file_edit_count=0,
        bash_count=0,
    )


def _artifact(
    artifact_type: str = "verification_recipe",
    project_name: str = "aide",
    status: str = "proposed",
) -> SemanticArtifact:
    now = _now()
    return SemanticArtifact(
        project_name=project_name,
        project_path=f"/Users/test/projects/{project_name}",
        artifact_type=artifact_type,
        title="Run full check",
        body="Use just check before committing.",
        status=status,
        confidence="high",
        source_provider="codex",
        source_session_id="sess-001",
        source_message_uuid="msg-001",
        first_seen_at=now,
        last_seen_at=now,
    )


def _evidence(
    artifact_id: int = 0,
    evidence_kind: str = "verification_result",
) -> ArtifactEvidence:
    return ArtifactEvidence(
        artifact_id=artifact_id,
        provider="codex",
        session_id="sess-001",
        message_uuid="msg-001",
        tool_name="Bash",
        evidence_kind=evidence_kind,
        summary="The check command completed successfully.",
    )


@pytest.fixture
def artifact_db(tmp_path):
    db_path = tmp_path / "artifacts.db"
    init_db(db_path)
    ingest_sessions(db_path, [_make_session()])
    return db_path


def test_propose_artifact_stores_artifact_evidence_and_event(artifact_db):
    artifact_id = propose_artifact(
        artifact_db,
        _artifact(),
        evidence=[_evidence()],
        note="Suggested by digest heuristic.",
    )

    stored = get_artifact(artifact_db, artifact_id)

    assert stored is not None
    assert stored["artifact_type"] == "verification_recipe"
    assert stored["status"] == "proposed"
    assert stored["confidence"] == "high"
    assert stored["source_provider"] == "codex"
    assert len(stored["evidence"]) == 1
    assert stored["evidence"][0]["evidence_kind"] == "verification_result"
    assert len(stored["events"]) == 1
    assert stored["events"][0]["event_type"] == "proposed"
    assert stored["events"][0]["note"] == "Suggested by digest heuristic."


def test_get_artifact_returns_none_for_missing_id(artifact_db):
    assert get_artifact(artifact_db, 999) is None


def test_list_artifacts_filters_by_project_status_and_type(artifact_db):
    proposed_id = propose_artifact(artifact_db, _artifact())
    other_id = propose_artifact(
        artifact_db,
        _artifact(artifact_type="agent_mistake", project_name="other"),
    )
    accept_artifact(artifact_db, other_id)

    result = list_artifacts(
        artifact_db,
        project_name="aide",
        status="proposed",
        artifact_type="verification_recipe",
    )

    assert [item["id"] for item in result] == [proposed_id]


def test_add_artifact_evidence_appends_summary(artifact_db):
    artifact_id = propose_artifact(artifact_db, _artifact())
    evidence_id = add_artifact_evidence(
        artifact_db,
        _evidence(artifact_id=artifact_id, evidence_kind="investigation_flag"),
    )

    stored = get_artifact(artifact_db, artifact_id)

    assert evidence_id > 0
    assert len(stored["evidence"]) == 1
    assert stored["evidence"][0]["evidence_kind"] == "investigation_flag"


def test_accept_artifact_updates_status_timestamp_and_event(artifact_db):
    artifact_id = propose_artifact(artifact_db, _artifact())

    accepted = accept_artifact(artifact_db, artifact_id, note="Looks correct.")
    stored = get_artifact(artifact_db, artifact_id)

    assert accepted["status"] == "accepted"
    assert accepted["accepted_at"] is not None
    assert accepted["rejected_at"] is None
    assert stored["events"][-1]["event_type"] == "accepted"
    assert stored["events"][-1]["note"] == "Looks correct."


def test_reject_artifact_updates_status_timestamp_and_event(artifact_db):
    artifact_id = propose_artifact(artifact_db, _artifact())

    rejected = reject_artifact(artifact_db, artifact_id, note="Too vague.")
    stored = get_artifact(artifact_db, artifact_id)

    assert rejected["status"] == "rejected"
    assert rejected["rejected_at"] is not None
    assert rejected["accepted_at"] is None
    assert stored["events"][-1]["event_type"] == "rejected"
    assert stored["events"][-1]["note"] == "Too vague."


def test_accept_or_reject_requires_proposed_artifact(artifact_db):
    artifact_id = propose_artifact(artifact_db, _artifact())
    accept_artifact(artifact_db, artifact_id)

    with pytest.raises(ValueError, match="only proposed"):
        reject_artifact(artifact_db, artifact_id)


def test_transition_missing_artifact_raises(artifact_db):
    with pytest.raises(ValueError, match="Artifact not found"):
        accept_artifact(artifact_db, 999)


def test_propose_artifact_rejects_invalid_type(artifact_db):
    with pytest.raises(ValueError, match="Unsupported artifact_type"):
        propose_artifact(artifact_db, _artifact(artifact_type="raw_prompt"))


def test_propose_artifact_requires_proposed_status(artifact_db):
    with pytest.raises(ValueError, match="must start"):
        propose_artifact(artifact_db, _artifact(status="accepted"))


def test_evidence_rejects_invalid_kind(artifact_db):
    artifact_id = propose_artifact(artifact_db, _artifact())

    with pytest.raises(ValueError, match="Unsupported evidence_kind"):
        add_artifact_evidence(
            artifact_db,
            _evidence(artifact_id=artifact_id, evidence_kind="raw_output"),
        )


def test_list_artifacts_rejects_invalid_filters(artifact_db):
    with pytest.raises(ValueError, match="Unsupported status"):
        list_artifacts(artifact_db, status="needs_review")

    with pytest.raises(ValueError, match="Unsupported artifact_type"):
        list_artifacts(artifact_db, artifact_type="raw_prompt")
