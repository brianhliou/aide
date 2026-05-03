"""End-to-end smoke tests for mixed Claude and Codex data."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from aide.cli import cli
from aide.config import AideConfig, LogSource
from aide.web.app import create_app


def _mixed_config(tmp_path: Path) -> AideConfig:
    claude_dir = tmp_path / "claude"
    codex_dir = tmp_path / "codex"
    claude_project_dir = claude_dir / "-Users-test-projects-app"
    claude_project_dir.mkdir(parents=True)
    codex_dir.mkdir()

    shutil.copyfile(
        "tests/fixtures/sample.jsonl",
        claude_project_dir / "sample.jsonl",
    )
    shutil.copyfile(
        "tests/fixtures/codex-redacted.jsonl",
        codex_dir / "codex-redacted.jsonl",
    )

    return AideConfig(
        subscription_user=False,
        log_dir=claude_dir,
        codex_log_dir=codex_dir,
        port=8787,
        db_path=tmp_path / "aide.db",
        sources=[
            LogSource(provider="claude", path=claude_dir),
            LogSource(provider="codex", path=codex_dir),
        ],
        sources_configured=True,
    )


def _session_ids_by_provider(db_path: Path) -> dict[str, str]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT provider, session_id FROM sessions ORDER BY provider"
        ).fetchall()
        return {provider: session_id for provider, session_id in rows}
    finally:
        con.close()


def test_mixed_provider_cli_web_and_autopsy_smoke(tmp_path):
    config = _mixed_config(tmp_path)
    runner = CliRunner()

    with patch("aide.cli.load_config", return_value=config):
        ingest = runner.invoke(cli, ["ingest", "--full"])

    assert ingest.exit_code == 0
    assert "claude: 1 sessions" in ingest.output
    assert "codex: 1 sessions" in ingest.output

    with patch("aide.cli.load_config", return_value=config):
        stats = runner.invoke(cli, ["stats"])

    assert stats.exit_code == 0
    assert "2 sessions" in stats.output

    session_ids = _session_ids_by_provider(config.db_path)
    assert set(session_ids) == {"claude", "codex"}

    app = create_app(config)
    app.config["TESTING"] = True
    with app.test_client() as client:
        paths = [
            "/",
            "/?provider=codex",
            "/projects?provider=claude",
            "/sessions?provider=codex",
            f"/sessions/claude/{session_ids['claude']}",
            f"/sessions/codex/{session_ids['codex']}",
            "/tools?provider=codex",
            "/insights?provider=codex",
        ]
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200, path

    with patch("aide.cli.load_config", return_value=config):
        claude_autopsy = runner.invoke(
            cli,
            ["autopsy", session_ids["claude"], "--provider", "claude"],
        )
        codex_autopsy = runner.invoke(
            cli,
            ["autopsy", session_ids["codex"], "--provider", "codex"],
        )

    assert claude_autopsy.exit_code == 0
    assert "CLAUDE.md Suggestions" in claude_autopsy.output
    assert codex_autopsy.exit_code == 0
    assert "AGENTS.md Suggestions" in codex_autopsy.output
