"""Claude Code provider adapter.

The historical `aide.parser` module remains the compatibility surface for
Claude parser tests and downstream imports. This module gives the provider
registry an explicit Claude adapter boundary matching `aide.codex_parser`.
"""

from __future__ import annotations

from pathlib import Path

from aide.models import ParsedSession
from aide.parser import discover_jsonl_files, parse_jsonl_file


def discover_claude_jsonl_files(log_dir: Path) -> list[Path]:
    """Find Claude Code JSONL files under a log directory."""
    return discover_jsonl_files(log_dir)


def parse_claude_jsonl_file(file_path: Path) -> list[ParsedSession]:
    """Parse a Claude Code JSONL file into normalized sessions."""
    return parse_jsonl_file(file_path)
