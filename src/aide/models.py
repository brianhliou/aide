"""Shared data models — the contract between parser, database, and consumers.

Parser produces ParsedSession objects. Database consumes them.
Cost estimation is called during parsing to populate estimated_cost_usd.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ToolCall:
    """A single tool invocation extracted from an assistant message."""

    tool_name: str
    file_path: str | None
    timestamp: datetime


@dataclass
class ParsedMessage:
    """A single message extracted from a JSONL log line."""

    uuid: str
    parent_uuid: str | None
    session_id: str
    timestamp: datetime
    role: str  # user, assistant, system
    type: str  # user, assistant, system, progress, file-history-snapshot
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    content_length: int
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ParsedSession:
    """A complete session extracted from one or more JSONL files.

    Produced by parser.py, consumed by db.py.
    """

    session_id: str
    project_path: str  # e.g., "-Users-brianliou-projects-slopfarm"
    project_name: str  # e.g., "slopfarm"
    source_file: str  # path to the JSONL file
    started_at: datetime
    ended_at: datetime | None
    messages: list[ParsedMessage]

    # Aggregates (computed from messages)
    duration_seconds: int | None
    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int
    total_cache_creation_tokens: int
    estimated_cost_usd: float
    message_count: int
    user_message_count: int
    assistant_message_count: int
    tool_call_count: int
    file_read_count: int
    file_write_count: int
    file_edit_count: int
    bash_count: int
