"""Provider parser registry.

Provider adapters convert raw local log files into aide's normalized
ParsedSession contract. The rest of ingestion should go through this registry
instead of importing provider-specific parser modules directly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from aide.claude_parser import discover_claude_jsonl_files, parse_claude_jsonl_file
from aide.codex_parser import discover_codex_jsonl_files, parse_codex_jsonl_file
from aide.models import ParsedSession

ProviderDiscovery = Callable[[Path], list[Path]]
ProviderParser = Callable[[Path], list[ParsedSession]]


@dataclass(frozen=True)
class ProviderAdapter:
    name: str
    discover_files: ProviderDiscovery
    parse_file: ProviderParser


PROVIDERS: dict[str, ProviderAdapter] = {
    "claude": ProviderAdapter(
        name="claude",
        discover_files=discover_claude_jsonl_files,
        parse_file=parse_claude_jsonl_file,
    ),
    "codex": ProviderAdapter(
        name="codex",
        discover_files=discover_codex_jsonl_files,
        parse_file=parse_codex_jsonl_file,
    ),
}


def get_provider(provider: str) -> ProviderAdapter:
    """Return a provider adapter or raise ValueError for unsupported providers."""
    try:
        return PROVIDERS[provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported provider: {provider}") from exc
