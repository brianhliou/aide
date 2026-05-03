"""Tests for CLI utilities."""

import json
import shutil
from datetime import datetime, timezone
from unittest.mock import patch

from click.testing import CliRunner

from aide.artifacts import get_artifact, list_artifacts, propose_artifact
from aide.cli import (
    archive_jsonl,
    backup_redacted_sources,
    cli,
    ingest_source,
    resolve_ingest_sources,
)
from aide.config import AideConfig, LogSource
from aide.db import get_summary_stats, ingest_sessions, init_db
from aide.jobs import LaunchdJobStatus
from aide.models import ParsedMessage, ParsedSession, SemanticArtifact, ToolCall
from aide.redaction import RedactionAuditResult


def _config(tmp_path, sources=None):
    return AideConfig(
        subscription_user=False,
        log_dir=tmp_path / "claude-legacy",
        codex_log_dir=tmp_path / "codex-legacy",
        port=8787,
        db_path=tmp_path / "aide.db",
        sources=sources if sources is not None else [],
        sources_configured=sources is not None,
    )


def _digest_session() -> ParsedSession:
    now = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    message = ParsedMessage(
        uuid="msg-digest",
        parent_uuid=None,
        session_id="digest-1",
        timestamp=now,
        role="assistant",
        type="assistant",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        content_length=100,
        tool_calls=[
            ToolCall(
                tool_name="Bash",
                file_path=None,
                timestamp=now,
                command="just check",
            )
        ],
    )
    return ParsedSession(
        provider="codex",
        session_id="digest-1",
        project_path="/Users/test/projects/aide",
        project_name="aide",
        source_file="/fake/digest-1.jsonl",
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
        tool_call_count=1,
        file_read_count=0,
        file_write_count=0,
        file_edit_count=0,
        bash_count=1,
    )


def _semantic_artifact(project_name: str = "aide") -> SemanticArtifact:
    now = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    return SemanticArtifact(
        project_name=project_name,
        project_path=f"/Users/test/projects/{project_name}",
        artifact_type="verification_recipe",
        title="Run full check",
        body="Use just check before committing.",
        confidence="high",
        source_provider="codex",
        source_session_id="digest-1",
        first_seen_at=now,
        last_seen_at=now,
    )


class TestArchiveJsonl:
    def test_copies_file_to_archive(self, tmp_path):
        log_dir = tmp_path / "logs"
        project_dir = log_dir / "-Users-brian-projects-myapp"
        project_dir.mkdir(parents=True)
        source = project_dir / "abc123.jsonl"
        source.write_text('{"type":"summary"}\n')

        archive_dir = tmp_path / "archive"
        archive_jsonl(source, log_dir, archive_dir)

        dest = archive_dir / "-Users-brian-projects-myapp" / "abc123.jsonl"
        assert dest.exists()
        assert dest.read_text() == '{"type":"summary"}\n'

    def test_preserves_directory_structure(self, tmp_path):
        log_dir = tmp_path / "logs"
        nested = log_dir / "a" / "b"
        nested.mkdir(parents=True)
        source = nested / "sess.jsonl"
        source.write_text("data")

        archive_dir = tmp_path / "archive"
        archive_jsonl(source, log_dir, archive_dir)

        assert (archive_dir / "a" / "b" / "sess.jsonl").exists()

    def test_overwrites_existing_archive(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        source = log_dir / "sess.jsonl"
        source.write_text("v1")

        archive_dir = tmp_path / "archive"
        archive_jsonl(source, log_dir, archive_dir)

        source.write_text("v2")
        archive_jsonl(source, log_dir, archive_dir)

        assert (archive_dir / "sess.jsonl").read_text() == "v2"

    def test_creates_archive_dir_if_missing(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        source = log_dir / "sess.jsonl"
        source.write_text("data")

        archive_dir = tmp_path / "does" / "not" / "exist"
        archive_jsonl(source, log_dir, archive_dir)

        assert (archive_dir / "sess.jsonl").exists()


class TestRedactCommand:
    def test_redact_file_outputs_counts_only(self, tmp_path):
        source = tmp_path / "codex.jsonl"
        source.write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "user_message",
                        "message": "private prompt with sk-secret",
                    },
                }
            )
            + "\n"
        )
        out = tmp_path / "redacted.jsonl"

        result = CliRunner().invoke(
            cli,
            ["redact", str(source), "--provider", "codex", "--out", str(out)],
        )

        assert result.exit_code == 0
        assert "Redacted 1 files, 1 lines" in result.output
        assert "private prompt" not in result.output
        assert "sk-secret" not in result.output
        assert "private prompt" not in out.read_text()
        assert "sk-secret" not in out.read_text()

    def test_redact_refuses_to_overwrite_source(self, tmp_path):
        source = tmp_path / "codex.jsonl"
        source.write_text('{"type":"response_item"}\n')

        result = CliRunner().invoke(
            cli,
            ["redact", str(source), "--provider", "codex", "--out", str(source)],
        )

        assert result.exit_code != 0
        assert "Refusing to overwrite source file" in result.output


class TestRedactAuditCommand:
    def test_redact_audit_passes_clean_redacted_file(self, tmp_path):
        redacted = tmp_path / "redacted.jsonl"
        redacted.write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "user_message",
                        "message": "<redacted:message len=31>",
                        "cwd": "/Users/<user>/projects/<project>",
                        "url": "<redacted:url>",
                        "command": "tool auth --token <redacted:secret>",
                        "cmd": "python -c 'token = param; cookie = request.cookies'",
                    },
                }
            )
            + "\n"
        )

        result = CliRunner().invoke(cli, ["redact-audit", str(redacted), "--strict"])

        assert result.exit_code == 0
        assert "Audited 1 files, 1 lines. Findings: 0." in result.output

    def test_redact_audit_reports_paths_without_values(self, tmp_path):
        redacted = tmp_path / "redacted.jsonl"
        redacted.write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "message": "private prompt with sk-secret",
                        "cwd": "/Users/brianliou/projects/aide",
                        "url": "https://example.com/private",
                        "image_url": "data:image/png;base64,PRIVATE",
                    },
                }
            )
            + "\n"
        )

        result = CliRunner().invoke(cli, ["redact-audit", str(redacted), "--strict"])

        assert result.exit_code != 0
        assert "Findings:" in result.output
        assert "raw_home_path: 1 at payload.cwd" in result.output
        assert "raw_url: 1 at payload.url" in result.output
        assert "image_payload: 1 at payload.image_url" in result.output
        assert "secret: 1 at payload.message" in result.output
        assert "private prompt" not in result.output
        assert "sk-secret" not in result.output
        assert "brianliou" not in result.output
        assert "example.com" not in result.output

    def test_redact_audit_defaults_to_configured_backup_dir_and_provider(self, tmp_path):
        backup_root = tmp_path / "redacted-logs"
        codex_dir = backup_root / "codex"
        codex_dir.mkdir(parents=True)
        (codex_dir / "codex.jsonl").write_text(
            json.dumps({"payload": {"message": "<redacted:message len=7>"}}) + "\n"
        )
        config = _config(tmp_path)

        with patch("aide.cli.load_config", return_value=config):
            result = CliRunner().invoke(cli, ["redact-audit", "--provider", "codex"])

        assert result.exit_code == 0
        assert "Audited 1 files, 1 lines. Findings: 0." in result.output


class TestBackupRedacted:
    def test_backup_redacted_sources_writes_provider_separated_outputs(self, tmp_path):
        claude_dir = tmp_path / "claude"
        claude_project = claude_dir / "-Users-test-projects-app"
        claude_project.mkdir(parents=True)
        shutil.copyfile(
            "tests/fixtures/sample.jsonl",
            claude_project / "sample.jsonl",
        )

        codex_dir = tmp_path / "codex"
        codex_dir.mkdir()
        shutil.copyfile(
            "tests/fixtures/codex-redacted.jsonl",
            codex_dir / "codex.jsonl",
        )

        out = tmp_path / "redacted-backups"
        config = _config(
            tmp_path,
            sources=[
                LogSource(provider="claude", path=claude_dir),
                LogSource(provider="codex", path=codex_dir),
            ],
        )

        results = backup_redacted_sources(config, output_dir=out)

        assert [r["provider"] for r in results] == ["claude", "codex"]
        assert (out / "claude" / "-Users-test-projects-app" / "sample.jsonl").exists()
        assert (out / "codex" / "codex.jsonl").exists()
        assert sum(r["files"] for r in results) == 2

    def test_backup_redacted_cli_outputs_counts_only(self, tmp_path):
        codex_dir = tmp_path / "codex"
        codex_dir.mkdir()
        source = codex_dir / "codex.jsonl"
        source.write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "user_message",
                        "message": "private launchd backup prompt sk-secret",
                    },
                }
            )
            + "\n"
        )
        config = _config(
            tmp_path,
            sources=[LogSource(provider="codex", path=codex_dir)],
        )

        with patch("aide.cli.load_config", return_value=config):
            result = CliRunner().invoke(
                cli,
                ["backup-redacted", "--out", str(tmp_path / "redacted")],
            )

        assert result.exit_code == 0
        assert "Backed up 1 redacted files, 1 lines" in result.output
        assert "codex: 1 files, 1 lines" in result.output
        assert "private launchd backup prompt" not in result.output
        assert "sk-secret" not in result.output
        redacted = (tmp_path / "redacted" / "codex" / "codex.jsonl").read_text()
        assert "private launchd backup prompt" not in redacted
        assert "sk-secret" not in redacted

    def test_backup_redacted_cli_filters_provider(self, tmp_path):
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()
        (claude_dir / "claude.jsonl").write_text('{"type":"summary"}\n')
        codex_dir = tmp_path / "codex"
        codex_dir.mkdir()
        (codex_dir / "codex.jsonl").write_text('{"type":"response_item"}\n')
        config = _config(
            tmp_path,
            sources=[
                LogSource(provider="claude", path=claude_dir),
                LogSource(provider="codex", path=codex_dir),
            ],
        )

        with patch("aide.cli.load_config", return_value=config):
            result = CliRunner().invoke(
                cli,
                [
                    "backup-redacted",
                    "--provider",
                    "codex",
                    "--out",
                    str(tmp_path / "redacted"),
                ],
            )

        assert result.exit_code == 0
        assert "codex: 1 files" in result.output
        assert "claude:" not in result.output
        assert (tmp_path / "redacted" / "codex" / "codex.jsonl").exists()
        assert not (tmp_path / "redacted" / "claude").exists()

    def test_backup_redacted_sources_skips_missing_source(self, tmp_path):
        config = _config(
            tmp_path,
            sources=[LogSource(provider="codex", path=tmp_path / "missing")],
        )

        result = backup_redacted_sources(config)

        assert result == [{
            "provider": "codex",
            "files": 0,
            "lines": 0,
            "fields": 0,
            "invalid_lines": 0,
            "missing": True,
        }]


class TestJobsStatusCommand:
    def test_jobs_status_outputs_job_metadata_and_audit_summary(self, tmp_path):
        status = LaunchdJobStatus(
            name="ingest",
            label="com.brianliou.aide.ingest",
            plist_path=tmp_path / "ingest.plist",
            plist_exists=True,
            loaded=True,
            state="not running",
            runs=5,
            last_exit_code=0,
            schedule="daily 12:00",
            stdout_path=tmp_path / "ingest.log",
            stderr_path=tmp_path / "ingest.err",
            stdout_last_lines=["Ingested 2 sessions from 2 files."],
            stderr_size=0,
        )
        backup_root = tmp_path / "redacted-logs"
        backup_root.mkdir()
        config = _config(tmp_path)

        with (
            patch("aide.cli.load_config", return_value=config),
            patch("aide.cli.collect_launchd_job_statuses", return_value=[status]),
            patch("aide.cli.audit_redacted_path", return_value=RedactionAuditResult(
                files_scanned=2,
                lines_scanned=10,
            )),
        ):
            result = CliRunner().invoke(cli, ["jobs", "status"])

        assert result.exit_code == 0
        assert "ingest: ok" in result.output
        assert "loaded: yes" in result.output
        assert "last exit code: 0" in result.output
        assert "last output: Ingested 2 sessions from 2 files." in result.output
        assert "redaction audit: pass (2 files, 10 lines, 0 findings)" in result.output

    def test_jobs_status_reports_missing_and_failed_jobs(self, tmp_path):
        status = LaunchdJobStatus(
            name="redacted backup",
            label="com.brianliou.aide.backup-redacted",
            plist_path=tmp_path / "missing.plist",
            plist_exists=False,
            loaded=False,
            last_exit_code=2,
            error="Could not find service",
        )
        config = _config(tmp_path)

        with (
            patch("aide.cli.load_config", return_value=config),
            patch("aide.cli.collect_launchd_job_statuses", return_value=[status]),
        ):
            result = CliRunner().invoke(cli, ["jobs", "status", "--skip-audit"])

        assert result.exit_code == 0
        assert "redacted backup: attention" in result.output
        assert "plist: " in result.output
        assert "(missing)" in result.output
        assert "loaded: no" in result.output
        assert "launchctl: Could not find service" in result.output
        assert "redaction audit: skipped" in result.output


class TestIngestCommand:
    def test_ingest_help_includes_provider_option(self):
        result = CliRunner().invoke(cli, ["ingest", "--help"])

        assert result.exit_code == 0
        assert "backup-redacted" in CliRunner().invoke(cli, ["--help"]).output
        assert "redact-audit" in CliRunner().invoke(cli, ["--help"]).output
        assert "jobs" in CliRunner().invoke(cli, ["--help"]).output
        assert "--provider" in result.output
        assert "--source" in result.output
        assert "--archive-raw" in result.output

    def test_resolve_ingest_sources_returns_all_configured_sources(self, tmp_path):
        sources = [
            LogSource(provider="claude", path=tmp_path / "claude"),
            LogSource(provider="codex", path=tmp_path / "codex"),
        ]
        config = _config(tmp_path, sources=sources)

        assert resolve_ingest_sources(config) == sources

    def test_resolve_ingest_sources_filters_by_provider(self, tmp_path):
        claude = LogSource(provider="claude", path=tmp_path / "claude")
        codex = LogSource(provider="codex", path=tmp_path / "codex")
        config = _config(tmp_path, sources=[claude, codex])

        assert resolve_ingest_sources(config, provider="codex") == [codex]

    def test_resolve_ingest_sources_uses_legacy_codex_fallback(self, tmp_path):
        config = _config(tmp_path)

        assert resolve_ingest_sources(config, provider="codex") == [
            LogSource(provider="codex", path=tmp_path / "codex-legacy")
        ]

    def test_resolve_ingest_sources_does_not_fallback_when_sources_configured(self, tmp_path):
        config = _config(
            tmp_path,
            sources=[LogSource(provider="claude", path=tmp_path / "claude")],
        )

        assert resolve_ingest_sources(config, provider="codex") == []

    def test_ingest_source_ingests_claude_source(self, tmp_path):
        db_path = tmp_path / "aide.db"
        init_db(db_path)
        log_dir = tmp_path / "claude"
        project_dir = log_dir / "-Users-test-projects-app"
        project_dir.mkdir(parents=True)
        shutil.copyfile(
            "tests/fixtures/sample.jsonl",
            project_dir / "sample.jsonl",
        )

        result = ingest_source(
            db_path,
            LogSource(provider="claude", path=log_dir),
            full=False,
        )

        assert result["ingested"] == 1
        assert result["archived"] == 0
        assert not (tmp_path / "archive").exists()
        assert get_summary_stats(db_path)["total_sessions"] == 1

    def test_ingest_source_ingests_codex_source(self, tmp_path):
        db_path = tmp_path / "aide.db"
        init_db(db_path)
        log_dir = tmp_path / "codex"
        log_dir.mkdir()
        shutil.copyfile(
            "tests/fixtures/codex-redacted.jsonl",
            log_dir / "codex.jsonl",
        )

        result = ingest_source(
            db_path,
            LogSource(provider="codex", path=log_dir),
            full=False,
        )

        assert result["ingested"] == 1
        assert get_summary_stats(db_path)["total_sessions"] == 1

    def test_ingest_source_archives_raw_only_when_requested(self, tmp_path):
        db_path = tmp_path / "aide.db"
        init_db(db_path)
        log_dir = tmp_path / "claude"
        project_dir = log_dir / "-Users-test-projects-app"
        project_dir.mkdir(parents=True)
        shutil.copyfile(
            "tests/fixtures/sample.jsonl",
            project_dir / "sample.jsonl",
        )

        result = ingest_source(
            db_path,
            LogSource(provider="claude", path=log_dir),
            full=False,
            archive_raw=True,
        )

        assert result["archived"] == 1
        assert (
            tmp_path
            / "archive"
            / "claude"
            / "-Users-test-projects-app"
            / "sample.jsonl"
        ).exists()


class TestDigestCommand:
    def test_digest_help_is_available(self):
        result = CliRunner().invoke(cli, ["digest", "--help"])

        assert result.exit_code == 0
        assert "--save-proposals" in result.output
        assert "--provider" in result.output

    def test_digest_previews_without_saving(self, tmp_path):
        config = _config(tmp_path)
        init_db(config.db_path)
        ingest_sessions(config.db_path, [_digest_session()])

        with patch("aide.cli.load_config", return_value=config):
            result = CliRunner().invoke(
                cli,
                ["digest", "digest-1", "--provider", "codex"],
            )

        assert result.exit_code == 0
        assert "Digest proposals for codex:digest-1 (aide)" in result.output
        assert "Dry run: 1 artifact proposal" in result.output
        assert "verification_recipe" in result.output
        assert list_artifacts(config.db_path) == []

    def test_digest_save_proposals_persists_artifacts(self, tmp_path):
        config = _config(tmp_path)
        init_db(config.db_path)
        ingest_sessions(config.db_path, [_digest_session()])

        with patch("aide.cli.load_config", return_value=config):
            result = CliRunner().invoke(
                cli,
                ["digest", "digest-1", "--provider", "codex", "--save-proposals"],
            )

        artifacts = list_artifacts(config.db_path)
        assert result.exit_code == 0
        assert "Saved 1 artifact proposal" in result.output
        assert "#1" in result.output
        assert len(artifacts) == 1
        assert artifacts[0]["artifact_type"] == "verification_recipe"

    def test_digest_missing_session_exits_nonzero(self, tmp_path):
        config = _config(tmp_path)
        init_db(config.db_path)

        with patch("aide.cli.load_config", return_value=config):
            result = CliRunner().invoke(cli, ["digest", "missing"])

        assert result.exit_code != 0
        assert "Session 'missing' not found." in result.output


class TestArtifactsCommand:
    def test_artifacts_help_is_available(self):
        result = CliRunner().invoke(cli, ["artifacts", "--help"])

        assert result.exit_code == 0
        assert "list" in result.output
        assert "show" in result.output
        assert "accept" in result.output
        assert "reject" in result.output

    def test_artifacts_list_outputs_saved_proposals(self, tmp_path):
        config = _config(tmp_path)
        init_db(config.db_path)
        ingest_sessions(config.db_path, [_digest_session()])
        artifact_id = propose_artifact(config.db_path, _semantic_artifact())

        with patch("aide.cli.load_config", return_value=config):
            result = CliRunner().invoke(
                cli,
                ["artifacts", "list", "--status", "proposed", "--project", "aide"],
            )

        assert result.exit_code == 0
        assert f"#{artifact_id} [proposed] verification_recipe aide" in result.output
        assert "source: codex:digest-1" in result.output

    def test_artifacts_list_empty(self, tmp_path):
        config = _config(tmp_path)
        init_db(config.db_path)

        with patch("aide.cli.load_config", return_value=config):
            result = CliRunner().invoke(cli, ["artifacts", "list"])

        assert result.exit_code == 0
        assert "No artifacts found." in result.output

    def test_artifacts_show_outputs_detail(self, tmp_path):
        config = _config(tmp_path)
        init_db(config.db_path)
        ingest_sessions(config.db_path, [_digest_session()])
        artifact_id = propose_artifact(
            config.db_path,
            _semantic_artifact(),
            note="successful verification command",
        )

        with patch("aide.cli.load_config", return_value=config):
            result = CliRunner().invoke(cli, ["artifacts", "show", str(artifact_id)])

        assert result.exit_code == 0
        assert f"Artifact #{artifact_id}: Run full check" in result.output
        assert "Type: verification_recipe" in result.output
        assert "Status: proposed" in result.output
        assert "Use just check before committing." in result.output
        assert "Events" in result.output
        assert "successful verification command" in result.output

    def test_artifacts_show_missing_exits_nonzero(self, tmp_path):
        config = _config(tmp_path)
        init_db(config.db_path)

        with patch("aide.cli.load_config", return_value=config):
            result = CliRunner().invoke(cli, ["artifacts", "show", "999"])

        assert result.exit_code != 0
        assert "Artifact #999 not found." in result.output

    def test_artifacts_accept_updates_status(self, tmp_path):
        config = _config(tmp_path)
        init_db(config.db_path)
        ingest_sessions(config.db_path, [_digest_session()])
        artifact_id = propose_artifact(config.db_path, _semantic_artifact())

        with patch("aide.cli.load_config", return_value=config):
            result = CliRunner().invoke(
                cli,
                ["artifacts", "accept", str(artifact_id), "--note", "Looks right."],
            )

        artifact = get_artifact(config.db_path, artifact_id)
        assert result.exit_code == 0
        assert f"Artifact #{artifact_id} accepted: Run full check" in result.output
        assert artifact["status"] == "accepted"
        assert artifact["events"][-1]["note"] == "Looks right."

    def test_artifacts_reject_updates_status(self, tmp_path):
        config = _config(tmp_path)
        init_db(config.db_path)
        ingest_sessions(config.db_path, [_digest_session()])
        artifact_id = propose_artifact(config.db_path, _semantic_artifact())

        with patch("aide.cli.load_config", return_value=config):
            result = CliRunner().invoke(
                cli,
                ["artifacts", "reject", str(artifact_id), "--note", "Too vague."],
            )

        artifact = get_artifact(config.db_path, artifact_id)
        assert result.exit_code == 0
        assert f"Artifact #{artifact_id} rejected: Run full check" in result.output
        assert artifact["status"] == "rejected"
        assert artifact["events"][-1]["note"] == "Too vague."

    def test_artifacts_accept_rejects_non_proposed_artifact(self, tmp_path):
        config = _config(tmp_path)
        init_db(config.db_path)
        ingest_sessions(config.db_path, [_digest_session()])
        artifact_id = propose_artifact(config.db_path, _semantic_artifact())

        with patch("aide.cli.load_config", return_value=config):
            CliRunner().invoke(cli, ["artifacts", "accept", str(artifact_id)])
            result = CliRunner().invoke(cli, ["artifacts", "reject", str(artifact_id)])

        assert result.exit_code != 0
        assert "only proposed artifacts can be accepted or rejected" in result.output
