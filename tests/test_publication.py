"""Tests for publishing accepted artifacts as Markdown."""

from datetime import date, datetime, timezone

import pytest

from aide.artifacts import accept_artifact, propose_artifact
from aide.db import init_db
from aide.models import SemanticArtifact
from aide.publication import (
    list_log_artifacts,
    log_filename_for_artifact,
    post_draft_filename,
    render_log_from_artifact,
    render_post_draft,
    write_log_from_artifact,
    write_logs_for_project,
    write_post_draft,
)


def _artifact(
    *,
    status: str = "proposed",
    artifact_type: str = "decision",
    title: str = "Use accepted artifacts for public logs",
    project_name: str = "aide",
    first_seen_at: datetime | None = None,
) -> SemanticArtifact:
    now = first_seen_at or datetime(2026, 5, 4, 9, 30, 0, tzinfo=timezone.utc)
    return SemanticArtifact(
        project_name=project_name,
        project_path=f"/Users/test/projects/{project_name}",
        artifact_type=artifact_type,
        title=title,
        body=(
            "Accepted artifacts should become the source material for public logs "
            "because raw provider logs can contain private prompts and tool output."
        ),
        status=status,
        confidence="high",
        source_provider="codex",
        source_session_id="session-1",
        first_seen_at=now,
        last_seen_at=now,
    )


@pytest.fixture
def publication_db(tmp_path):
    db_path = tmp_path / "publication.db"
    init_db(db_path)
    return db_path


def test_render_log_from_accepted_artifact(publication_db):
    artifact_id = propose_artifact(publication_db, _artifact())
    accept_artifact(publication_db, artifact_id)

    markdown = render_log_from_artifact(publication_db, artifact_id)

    assert markdown.startswith("---\nlayout: page\n")
    assert 'title: "Use accepted artifacts for public logs"' in markdown
    assert "date: 2026-05-04" in markdown
    assert "*2026-05-04*" in markdown
    assert "Accepted artifacts should become the source material" in markdown
    assert f"accepted aide artifact #{artifact_id}" in markdown
    assert "`codex:session-1`" in markdown
    assert markdown.endswith(
        "The reusable rule is to record the decision with the evidence that "
        "made it worth keeping.\n"
    )


def test_render_log_rejects_unaccepted_artifact(publication_db):
    artifact_id = propose_artifact(publication_db, _artifact())

    with pytest.raises(ValueError, match="only accepted artifacts"):
        render_log_from_artifact(publication_db, artifact_id)


def test_log_filename_slugifies_title(publication_db):
    artifact_id = propose_artifact(
        publication_db,
        _artifact(title="Use Artifacts: Logs & Posts"),
    )
    accept_artifact(publication_db, artifact_id)

    assert log_filename_for_artifact(publication_db, artifact_id) == (
        "use-artifacts-logs-posts.md"
    )


def test_write_log_from_artifact_respects_overwrite(publication_db, tmp_path):
    artifact_id = propose_artifact(publication_db, _artifact())
    accept_artifact(publication_db, artifact_id)
    output_path = tmp_path / "_log" / "artifact-log.md"

    write_log_from_artifact(publication_db, artifact_id, output_path)

    assert output_path.exists()
    with pytest.raises(FileExistsError, match="Output already exists"):
        write_log_from_artifact(publication_db, artifact_id, output_path)

    write_log_from_artifact(publication_db, artifact_id, output_path, overwrite=True)
    assert "layout: page" in output_path.read_text()


def test_list_log_artifacts_filters_project_type_and_since(publication_db):
    old_id = propose_artifact(
        publication_db,
        _artifact(
            title="Old decision",
            first_seen_at=datetime(2026, 5, 1, 9, 30, tzinfo=timezone.utc),
        ),
    )
    keep_id = propose_artifact(
        publication_db,
        _artifact(
            title="New decision",
            first_seen_at=datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc),
        ),
    )
    other_project_id = propose_artifact(
        publication_db,
        _artifact(title="Other project", project_name="other"),
    )
    private_type_id = propose_artifact(
        publication_db,
        _artifact(title="Setup step", artifact_type="setup_step"),
    )
    for artifact_id in [old_id, keep_id, other_project_id, private_type_id]:
        accept_artifact(publication_db, artifact_id)

    artifacts = list_log_artifacts(
        publication_db,
        "aide",
        since=date(2026, 5, 2),
    )

    assert [item["title"] for item in artifacts] == ["New decision"]


def test_write_logs_for_project_writes_batch(publication_db, tmp_path):
    first_id = propose_artifact(publication_db, _artifact(title="First decision"))
    second_id = propose_artifact(publication_db, _artifact(title="Second decision"))
    accept_artifact(publication_db, first_id)
    accept_artifact(publication_db, second_id)

    output_paths = write_logs_for_project(publication_db, "aide", tmp_path / "_log")

    assert output_paths == [
        tmp_path / "_log" / "first-decision.md",
        tmp_path / "_log" / "second-decision.md",
    ]
    assert all(path.exists() for path in output_paths)


def test_render_post_draft_groups_accepted_artifacts(publication_db):
    decision_id = propose_artifact(publication_db, _artifact(title="Keep logs public"))
    risk_id = propose_artifact(
        publication_db,
        _artifact(
            artifact_type="risky_action",
            title="Avoid raw logs",
        ),
    )
    other_project_id = propose_artifact(
        publication_db,
        _artifact(title="Other project", project_name="other"),
    )
    for artifact_id in [decision_id, risk_id, other_project_id]:
        accept_artifact(publication_db, artifact_id)

    markdown = render_post_draft(
        publication_db,
        "aide",
        "Automated engineering logs",
    )

    assert markdown.startswith("---\nlayout: post\n")
    assert 'title: "Automated engineering logs"' in markdown
    assert "Review, restructure, and remove private context before publishing." in markdown
    assert "## Working Thesis" in markdown
    assert "### Decision" in markdown
    assert "Keep logs public" in markdown
    assert "### Risky Action" in markdown
    assert "Avoid raw logs" in markdown
    assert "Other project" not in markdown


def test_write_post_draft_respects_overwrite(publication_db, tmp_path):
    artifact_id = propose_artifact(publication_db, _artifact(title="Keep logs public"))
    accept_artifact(publication_db, artifact_id)
    output_path = tmp_path / "_drafts" / "automated-logs.md"

    write_post_draft(
        publication_db,
        "aide",
        "Automated logs",
        output_path,
    )

    assert output_path.exists()
    with pytest.raises(FileExistsError, match="Output already exists"):
        write_post_draft(
            publication_db,
            "aide",
            "Automated logs",
            output_path,
        )

    write_post_draft(
        publication_db,
        "aide",
        "Automated logs",
        output_path,
        overwrite=True,
    )
    assert "## Source Artifacts" in output_path.read_text()


def test_post_draft_filename_slugifies_topic():
    assert post_draft_filename("Automated Logs & Posts") == (
        "automated-logs-posts.md"
    )
