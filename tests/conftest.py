"""Shared test fixtures for aide tests."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir():
    """Path to test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def sample_jsonl():
    """Path to sample JSONL file (session-001 in my-project)."""
    return FIXTURES_DIR / "sample.jsonl"


@pytest.fixture
def sample2_jsonl():
    """Path to second sample JSONL file (session-002 in other-project)."""
    return FIXTURES_DIR / "sample2.jsonl"


@pytest.fixture
def tmp_db(tmp_path):
    """Path to a temporary SQLite database file."""
    return tmp_path / "test-aide.db"
