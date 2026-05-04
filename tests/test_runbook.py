"""Tests for deterministic runbook generation."""

from datetime import datetime, timezone

from aide.artifacts import accept_artifact, propose_artifact, reject_artifact
from aide.db import ingest_sessions, init_db
from aide.models import ArtifactEvidence, ParsedMessage, ParsedSession, SemanticArtifact
from aide.runbook import render_project_runbook, write_project_runbook


def _now() -> datetime:
    return datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def _session(project_name: str = "aide") -> ParsedSession:
    now = _now()
    message = ParsedMessage(
        uuid="msg-001",
        parent_uuid=None,
        session_id="sess-001",
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
        provider="codex",
        session_id="sess-001",
        project_path=f"/Users/test/projects/{project_name}",
        project_name=project_name,
        source_file="/fake/sess-001.jsonl",
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
    artifact_type: str,
    title: str,
    body: str,
    project_name: str = "aide",
) -> SemanticArtifact:
    now = _now()
    return SemanticArtifact(
        project_name=project_name,
        project_path=f"/Users/test/projects/{project_name}",
        artifact_type=artifact_type,
        title=title,
        body=body,
        confidence="high",
        source_provider="codex",
        source_session_id="sess-001",
        first_seen_at=now,
        last_seen_at=now,
    )


def _evidence(kind: str = "verification_result") -> ArtifactEvidence:
    return ArtifactEvidence(
        artifact_id=0,
        provider="codex",
        session_id="sess-001",
        tool_name="Bash",
        evidence_kind=kind,
        summary="A check command completed successfully.",
    )


def _db(tmp_path):
    db_path = tmp_path / "runbook.db"
    init_db(db_path)
    ingest_sessions(db_path, [_session()])
    return db_path


def test_render_project_runbook_uses_accepted_artifacts_only(tmp_path):
    db_path = _db(tmp_path)
    accepted = propose_artifact(
        db_path,
        _artifact("verification_recipe", "Run checks", "Use `just check`."),
        evidence=[_evidence()],
    )
    proposed = propose_artifact(
        db_path,
        _artifact("decision", "Keep proposed out", "This should not render."),
    )
    rejected = propose_artifact(
        db_path,
        _artifact("risky_action", "Keep rejected out", "This should not render."),
    )
    accept_artifact(db_path, accepted)
    reject_artifact(db_path, rejected)

    markdown = render_project_runbook(db_path, "aide")

    assert "## Verification Recipes" in markdown
    assert "Run checks" in markdown
    assert "Use `just check`." in markdown
    assert "Keep proposed out" not in markdown
    assert "Keep rejected out" not in markdown
    assert f"Artifact: #{accepted}" in markdown
    assert f"Artifact: #{proposed}" not in markdown


def test_render_project_runbook_uses_stable_section_order(tmp_path):
    db_path = _db(tmp_path)
    risky = propose_artifact(
        db_path,
        _artifact("risky_action", "Review deploys", "Deploys need care."),
    )
    decision = propose_artifact(
        db_path,
        _artifact("decision", "Use SQLite", "SQLite is the local store."),
    )
    accept_artifact(db_path, risky)
    accept_artifact(db_path, decision)

    markdown = render_project_runbook(db_path, "aide")

    assert markdown.index("## Decisions") < markdown.index("## Risky Actions")


def test_render_project_runbook_includes_source_and_evidence_summaries(tmp_path):
    db_path = _db(tmp_path)
    artifact_id = propose_artifact(
        db_path,
        _artifact("verification_recipe", "Run checks", "Use `just check`."),
        evidence=[_evidence()],
    )
    accept_artifact(db_path, artifact_id)

    markdown = render_project_runbook(db_path, "aide")

    assert "Source: codex:sess-001" in markdown
    assert "Evidence:" in markdown
    assert "verification_result via Bash: A check command completed successfully." in markdown


def test_render_project_runbook_for_empty_project(tmp_path):
    db_path = _db(tmp_path)

    markdown = render_project_runbook(db_path, "missing")

    assert markdown.startswith("# missing Runbook")
    assert "No accepted artifacts found for this project." in markdown


def test_write_project_runbook_writes_markdown(tmp_path):
    db_path = _db(tmp_path)
    artifact_id = propose_artifact(
        db_path,
        _artifact("decision", "Use SQLite", "SQLite is the local store."),
    )
    accept_artifact(db_path, artifact_id)
    output_path = tmp_path / "docs" / "runbooks" / "aide.md"

    write_project_runbook(db_path, "aide", output_path)

    assert output_path.exists()
    assert "## Decisions" in output_path.read_text()
