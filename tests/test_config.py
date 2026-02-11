"""Tests for aide.config module."""

from pathlib import Path

from aide.config import DEFAULTS, AideConfig, load_config


def test_load_config_no_file(tmp_path):
    """When config file doesn't exist, return defaults without error."""
    config = load_config(config_path=tmp_path / "nonexistent.yaml")
    assert isinstance(config, AideConfig)
    assert config.subscription_user is False
    assert config.port == 8787


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
