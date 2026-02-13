"""Shared data models — the contract between parser, database, and consumers.

Parser produces ParsedSession objects. Database consumes them.
Cost estimation is called during parsing to populate estimated_cost_usd.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class WorkBlock:
    """A continuous stretch of activity within a session.

    Sessions are split into work blocks at gaps > 30 minutes of inactivity.
    """

    session_id: str
    block_index: int  # 0-based within the session
    started_at: datetime
    ended_at: datetime
    duration_seconds: int
    message_count: int


@dataclass
class ToolCall:
    """A single tool invocation extracted from an assistant message."""

    tool_name: str
    file_path: str | None
    timestamp: datetime
    tool_use_id: str | None = None
    command: str | None = None
    description: str | None = None
    is_error: bool = False
    old_string_len: int | None = None
    new_string_len: int | None = None


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
    model: str | None = None
    stop_reason: str | None = None
    prompt_length: int = 0
    thinking_chars: int = 0


@dataclass
class ParsedSession:
    """A complete session extracted from one or more JSONL files.

    Produced by parser.py, consumed by db.py.
    """

    session_id: str
    project_path: str  # e.g., "-Users-username-projects-myproject"
    project_name: str  # e.g., "myproject"
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
    compaction_count: int = 0
    peak_context_tokens: int = 0
    custom_title: str | None = None
    total_turn_duration_ms: int = 0
    turn_count: int = 0
    max_turn_duration_ms: int = 0
    tool_error_count: int = 0
    git_branch: str | None = None
    rework_file_count: int = 0
    test_after_edit_rate: float = 0.0
    total_thinking_chars: int = 0
    thinking_message_count: int = 0
    permission_mode: str | None = None
    active_duration_seconds: int = 0
    work_blocks: list[WorkBlock] = field(default_factory=list)
