"""Configuration loader — reads optional YAML config and merges with defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_PATH = Path("~/.config/aide/config.yaml")

DEFAULTS = {
    "subscription_user": False,
    "log_dir": "~/.claude/projects",
    "port": 8787,
    "db_path": "~/.local/share/aide/aide.db",
}


@dataclass
class AideConfig:
    subscription_user: bool
    log_dir: Path
    port: int
    db_path: Path


def save_config_value(key: str, value: object, config_path: Path | None = None) -> None:
    """Update a single key in the config file, preserving other settings."""
    if config_path is None:
        config_path = CONFIG_PATH
    config_path = config_path.expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if config_path.is_file():
        with open(config_path) as f:
            loaded = yaml.safe_load(f)
        if isinstance(loaded, dict):
            existing = loaded

    existing[key] = value
    with open(config_path, "w") as f:
        yaml.dump(existing, f, default_flow_style=False)


def load_config(config_path: Path | None = None) -> AideConfig:
    """Load config from ~/.config/aide/config.yaml, merged with defaults.

    Expand ~ in paths. Create parent directories for db_path if they don't exist.
    If no config file exists, return defaults (don't error).
    """
    if config_path is None:
        config_path = CONFIG_PATH

    config_path = config_path.expanduser()

    merged = dict(DEFAULTS)

    if config_path.is_file():
        with open(config_path) as f:
            user_config = yaml.safe_load(f)
        if isinstance(user_config, dict):
            for key in DEFAULTS:
                if key in user_config:
                    merged[key] = user_config[key]

    log_dir = Path(merged["log_dir"]).expanduser()
    db_path = Path(merged["db_path"]).expanduser()

    db_path.parent.mkdir(parents=True, exist_ok=True)

    return AideConfig(
        subscription_user=bool(merged["subscription_user"]),
        log_dir=log_dir,
        port=int(merged["port"]),
        db_path=db_path,
    )
