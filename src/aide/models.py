"""Shared data models — the contract between parser, database, and consumers.

Parser produces ParsedSession objects. Database consumes them.
Cost estimation is called during parsing to populate estimated_cost_usd.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

ARTIFACT_TYPE_VALUES = (
    "decision",
    "setup_step",
    "credential_step",
    "verification_recipe",
    "agent_mistake",
    "risky_action",
    "future_agent_instruction",
    "planner_signal",
)
ARTIFACT_TYPES = frozenset(ARTIFACT_TYPE_VALUES)

ARTIFACT_STATUS_VALUES = (
    "proposed",
    "accepted",
    "rejected",
    "superseded",
    "archived",
)
ARTIFACT_STATUSES = frozenset(ARTIFACT_STATUS_VALUES)

ARTIFACT_CONFIDENCE_VALUES = ("low", "medium", "high")
ARTIFACT_CONFIDENCES = frozenset(ARTIFACT_CONFIDENCE_VALUES)

ARTIFACT_EVIDENCE_KIND_VALUES = (
    "session_summary",
    "tool_pattern",
    "error_pattern",
    "verification_result",
    "permission_friction",
    "investigation_flag",
)
ARTIFACT_EVIDENCE_KINDS = frozenset(ARTIFACT_EVIDENCE_KIND_VALUES)

ARTIFACT_EVENT_TYPE_VALUES = (
    "proposed",
    "accepted",
    "rejected",
    "superseded",
    "archived",
    "updated",
)
ARTIFACT_EVENT_TYPES = frozenset(ARTIFACT_EVENT_TYPE_VALUES)


@dataclass
class SemanticArtifact:
    """A durable, reviewable project-memory candidate.

    Artifact text is intentionally summary-level. Raw prompts, command output,
    diffs, and secrets stay out of this contract.
    """

    project_name: str
    artifact_type: str
    title: str
    body: str
    first_seen_at: datetime
    last_seen_at: datetime
    project_path: str | None = None
    status: str = "proposed"
    confidence: str = "medium"
    source_provider: str | None = None
    source_session_id: str | None = None
    source_message_uuid: str | None = None
    id: int | None = None


@dataclass
class ArtifactEvidence:
    """Summarized evidence supporting a semantic artifact."""

    artifact_id: int
    provider: str
    session_id: str
    evidence_kind: str
    summary: str
    message_uuid: str | None = None
    tool_name: str | None = None
    id: int | None = None


@dataclass
class ArtifactEvent:
    """Lifecycle event for artifact review and maintenance."""

    artifact_id: int
    event_type: str
    note: str | None = None
    id: int | None = None


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
    provider: str = "claude"
