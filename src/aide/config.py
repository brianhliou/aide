"""Configuration loader — reads optional YAML config and merges with defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path("~/.config/aide/config.yaml")

DEFAULTS = {
    "subscription_user": False,
    "log_dir": "~/.claude/projects",
    "codex_log_dir": "~/.codex/sessions",
    "port": 8787,
    "db_path": "~/.local/share/aide/aide.db",
}

SUPPORTED_SOURCE_PROVIDERS = frozenset({"claude", "codex"})


@dataclass(frozen=True)
class LogSource:
    provider: str
    path: Path


@dataclass(frozen=True)
class PublishingConfig:
    website_path: Path | None = None
    log_dir: str = "_log"
    draft_dir: str = "_drafts"


@dataclass
class AideConfig:
    subscription_user: bool
    log_dir: Path
    port: int
    db_path: Path
    codex_log_dir: Path = Path("~/.codex/sessions").expanduser()
    sources: list[LogSource] = field(default_factory=list)
    sources_configured: bool = False
    publishing: PublishingConfig = field(default_factory=PublishingConfig)


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
    configured_sources: list[LogSource] | None = None
    publishing = PublishingConfig()

    if config_path.is_file():
        with open(config_path) as f:
            user_config = yaml.safe_load(f)
        if isinstance(user_config, dict):
            for key in DEFAULTS:
                if key in user_config:
                    merged[key] = user_config[key]
            configured_sources = _parse_sources(user_config.get("sources"))
            publishing = _parse_publishing(user_config.get("publishing"))

    log_dir = Path(merged["log_dir"]).expanduser()
    codex_log_dir = Path(merged["codex_log_dir"]).expanduser()
    db_path = Path(merged["db_path"]).expanduser()
    sources = configured_sources or [LogSource(provider="claude", path=log_dir)]

    db_path.parent.mkdir(parents=True, exist_ok=True)

    return AideConfig(
        subscription_user=bool(merged["subscription_user"]),
        log_dir=log_dir,
        codex_log_dir=codex_log_dir,
        port=int(merged["port"]),
        db_path=db_path,
        sources=sources,
        sources_configured=configured_sources is not None,
        publishing=publishing,
    )


def _parse_sources(value: Any) -> list[LogSource] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("sources must be a list of {provider, path} entries")

    sources: list[LogSource] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"sources[{index}] must be a mapping")
        provider = item.get("provider")
        path = item.get("path")
        if provider not in SUPPORTED_SOURCE_PROVIDERS:
            raise ValueError(f"sources[{index}].provider is unsupported: {provider}")
        if not path:
            raise ValueError(f"sources[{index}].path is required")
        sources.append(LogSource(provider=provider, path=Path(path).expanduser()))

    return sources


def _parse_publishing(value: Any) -> PublishingConfig:
    if value is None:
        return PublishingConfig()
    if not isinstance(value, dict):
        raise ValueError("publishing must be a mapping")

    website_path = value.get("website_path")
    log_dir = value.get("log_dir", "_log")
    draft_dir = value.get("draft_dir", "_drafts")

    if website_path is not None and not isinstance(website_path, str):
        raise ValueError("publishing.website_path must be a string")
    if not isinstance(log_dir, str) or not log_dir:
        raise ValueError("publishing.log_dir must be a non-empty string")
    if not isinstance(draft_dir, str) or not draft_dir:
        raise ValueError("publishing.draft_dir must be a non-empty string")

    return PublishingConfig(
        website_path=Path(website_path).expanduser() if website_path else None,
        log_dir=log_dir,
        draft_dir=draft_dir,
    )
