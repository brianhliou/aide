"""Tests for task-specific brief generation."""

from datetime import datetime, timezone

from aide.artifacts import accept_artifact, propose_artifact, reject_artifact
from aide.brief import render_project_brief, write_project_brief
from aide.db import ingest_sessions, init_db
from aide.models import ParsedMessage, ParsedSession, SemanticArtifact


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


def _db(tmp_path):
    db_path = tmp_path / "brief.db"
    init_db(db_path)
    ingest_sessions(db_path, [_session()])
    return db_path


def test_render_project_brief_includes_task_and_accepted_artifacts_only(tmp_path):
    db_path = _db(tmp_path)
    accepted = propose_artifact(
        db_path,
        _artifact(
            "future_agent_instruction",
            "Start at repo root",
            "Run commands from the repository root.",
        ),
    )
    proposed = propose_artifact(
        db_path,
        _artifact("decision", "Ignore proposed", "This should not render."),
    )
    rejected = propose_artifact(
        db_path,
        _artifact("risky_action", "Ignore rejected", "This should not render."),
    )
    accept_artifact(db_path, accepted)
    reject_artifact(db_path, rejected)

    markdown = render_project_brief(db_path, "aide", "add artifact review UI")

    assert markdown.startswith("# aide Brief")
    assert "Task: add artifact review UI" in markdown
    assert "## Instructions" in markdown
    assert "Start at repo root: Run commands from the repository root." in markdown
    assert "Ignore proposed" not in markdown
    assert "Ignore rejected" not in markdown
    assert f"Artifact: #{proposed}" not in markdown


def test_render_project_brief_uses_priority_section_order(tmp_path):
    db_path = _db(tmp_path)
    decision = propose_artifact(
        db_path,
        _artifact("decision", "Use SQLite", "SQLite is the local store."),
    )
    verification = propose_artifact(
        db_path,
        _artifact("verification_recipe", "Run checks", "Use `just check`."),
    )
    instruction = propose_artifact(
        db_path,
        _artifact("future_agent_instruction", "Start root", "Use repo root."),
    )
    accept_artifact(db_path, decision)
    accept_artifact(db_path, verification)
    accept_artifact(db_path, instruction)

    markdown = render_project_brief(db_path, "aide", "ship runbook")

    assert markdown.index("## Instructions") < markdown.index("## Verification")
    assert markdown.index("## Verification") < markdown.index("## Decisions")


def test_render_project_brief_includes_source_references(tmp_path):
    db_path = _db(tmp_path)
    artifact_id = propose_artifact(
        db_path,
        _artifact("verification_recipe", "Run checks", "Use `just check`."),
    )
    accept_artifact(db_path, artifact_id)

    markdown = render_project_brief(db_path, "aide", "verify")

    assert "Source: codex:sess-001." in markdown


def test_render_project_brief_limits_items_per_section(tmp_path):
    db_path = _db(tmp_path)
    for index in range(6):
        artifact_id = propose_artifact(
            db_path,
            _artifact(
                "verification_recipe",
                f"Check {index}",
                f"Run command {index}.",
            ),
        )
        accept_artifact(db_path, artifact_id)

    markdown = render_project_brief(db_path, "aide", "verify")

    assert "Check 0" in markdown
    assert "Check 4" in markdown
    assert "Check 5" not in markdown


def test_render_project_brief_for_empty_project(tmp_path):
    db_path = _db(tmp_path)

    markdown = render_project_brief(db_path, "missing", "ship")

    assert markdown.startswith("# missing Brief")
    assert "Task: ship" in markdown
    assert "No accepted artifacts found for this project." in markdown


def test_write_project_brief_writes_markdown(tmp_path):
    db_path = _db(tmp_path)
    artifact_id = propose_artifact(
        db_path,
        _artifact("verification_recipe", "Run checks", "Use `just check`."),
    )
    accept_artifact(db_path, artifact_id)
    output_path = tmp_path / "briefs" / "aide.md"

    write_project_brief(db_path, "aide", "verify", output_path)

    assert output_path.exists()
    assert "# aide Brief" in output_path.read_text()
