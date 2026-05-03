"""Tests for provider parser registry."""

from pathlib import Path

import pytest

from aide.claude_parser import discover_claude_jsonl_files, parse_claude_jsonl_file
from aide.providers import PROVIDERS, ProviderAdapter, get_provider


def test_expected_providers_registered():
    assert set(PROVIDERS) == {"claude", "codex"}


def test_get_provider_returns_adapter():
    adapter = get_provider("claude")

    assert isinstance(adapter, ProviderAdapter)
    assert adapter.name == "claude"
    assert callable(adapter.discover_files)
    assert callable(adapter.parse_file)


def test_claude_provider_uses_claude_adapter_boundary():
    adapter = get_provider("claude")

    assert adapter.discover_files is discover_claude_jsonl_files
    assert adapter.parse_file is parse_claude_jsonl_file


def test_get_provider_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unsupported provider"):
        get_provider("unknown")


def test_registered_discovery_handles_missing_directory(tmp_path):
    missing = tmp_path / "missing"

    for adapter in PROVIDERS.values():
        assert adapter.discover_files(missing) == []


def test_registered_parsers_parse_known_fixtures():
    claude_sessions = get_provider("claude").parse_file(
        Path("tests/fixtures/sample.jsonl")
    )
    codex_sessions = get_provider("codex").parse_file(
        Path("tests/fixtures/codex-redacted.jsonl")
    )

    assert len(claude_sessions) == 1
    assert claude_sessions[0].provider == "claude"
    assert len(codex_sessions) == 1
    assert codex_sessions[0].provider == "codex"
