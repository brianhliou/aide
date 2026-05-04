"""Tests for aide.config module."""

from pathlib import Path

import pytest

from aide.config import DEFAULTS, AideConfig, LogSource, PublishingConfig, load_config


def test_load_config_no_file(tmp_path):
    """When config file doesn't exist, return defaults without error."""
    config = load_config(config_path=tmp_path / "nonexistent.yaml")
    assert isinstance(config, AideConfig)
    assert config.subscription_user is False
    assert config.port == 8787
    assert config.sources == [LogSource(provider="claude", path=config.log_dir)]
    assert config.sources_configured is False
    assert config.publishing == PublishingConfig()


def test_load_config_defaults_paths(tmp_path):
    """Default paths should be expanded (no ~ remaining)."""
    config = load_config(config_path=tmp_path / "nonexistent.yaml")
    assert "~" not in str(config.log_dir)
    assert "~" not in str(config.db_path)


def test_load_config_partial_override(tmp_path):
    """A partial config file merges with defaults correctly."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("port: 9999\n")

    config = load_config(config_path=config_file)
    assert config.port == 9999
    # Other defaults still apply
    assert config.subscription_user is False
    assert config.log_dir == Path(DEFAULTS["log_dir"]).expanduser()
    assert config.sources == [LogSource(provider="claude", path=config.log_dir)]


def test_load_config_subscription_user(tmp_path):
    """Test subscription_user=true is loaded correctly."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("subscription_user: true\n")

    config = load_config(config_path=config_file)
    assert config.subscription_user is True


def test_load_config_custom_paths(tmp_path):
    """Test that custom paths from config are expanded."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "log_dir: ~/my-logs\n"
        "db_path: ~/my-data/aide.db\n"
    )

    config = load_config(config_path=config_file)
    assert config.log_dir == Path("~/my-logs").expanduser()
    assert config.db_path == Path("~/my-data/aide.db").expanduser()
    assert config.sources == [LogSource(provider="claude", path=config.log_dir)]
    assert "~" not in str(config.log_dir)
    assert "~" not in str(config.db_path)


def test_load_config_unknown_keys_ignored(tmp_path):
    """Unknown keys in the YAML file are silently ignored."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("unknown_key: some_value\nport: 1234\n")

    config = load_config(config_path=config_file)
    assert config.port == 1234
    assert not hasattr(config, "unknown_key")


def test_load_config_creates_db_parent_dir(tmp_path):
    """load_config should create parent directories for db_path."""
    config_file = tmp_path / "config.yaml"
    db_dir = tmp_path / "deep" / "nested" / "dir"
    config_file.write_text(f"db_path: {db_dir}/aide.db\n")

    config = load_config(config_path=config_file)
    assert config.db_path.parent.exists()
    assert config.db_path.parent == db_dir


def test_load_config_empty_yaml(tmp_path):
    """An empty YAML file should return defaults."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("")

    config = load_config(config_path=config_file)
    assert config.port == 8787
    assert config.subscription_user is False


def test_load_config_sources_override_legacy_log_dirs(tmp_path):
    """When sources exists, it defines ingestion sources."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "log_dir: ~/legacy-claude\n"
        "codex_log_dir: ~/legacy-codex\n"
        "sources:\n"
        "  - provider: claude\n"
        "    path: ~/claude-one\n"
        "  - provider: codex\n"
        "    path: ~/codex-one\n"
    )

    config = load_config(config_path=config_file)

    assert config.log_dir == Path("~/legacy-claude").expanduser()
    assert config.codex_log_dir == Path("~/legacy-codex").expanduser()
    assert config.sources == [
        LogSource(provider="claude", path=Path("~/claude-one").expanduser()),
        LogSource(provider="codex", path=Path("~/codex-one").expanduser()),
    ]
    assert config.sources_configured is True


def test_load_config_rejects_unknown_source_provider(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "sources:\n"
        "  - provider: unknown\n"
        "    path: ~/logs\n"
    )

    with pytest.raises(ValueError, match="unsupported"):
        load_config(config_path=config_file)


def test_load_config_rejects_malformed_sources(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("sources: nope\n")

    with pytest.raises(ValueError, match="sources must be a list"):
        load_config(config_path=config_file)


def test_load_config_publishing(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "publishing:\n"
        "  website_path: ~/projects/brianhliou.github.io\n"
        "  log_dir: _log\n"
        "  draft_dir: _drafts\n"
    )

    config = load_config(config_path=config_file)

    assert config.publishing == PublishingConfig(
        website_path=Path("~/projects/brianhliou.github.io").expanduser(),
        log_dir="_log",
        draft_dir="_drafts",
    )


def test_load_config_rejects_malformed_publishing(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("publishing: nope\n")

    with pytest.raises(ValueError, match="publishing must be a mapping"):
        load_config(config_path=config_file)
