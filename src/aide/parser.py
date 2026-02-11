"""JSONL parser — reads Claude Code session logs into ParsedSession objects."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from aide.models import ParsedMessage, ParsedSession, ToolCall

try:
    from aide.cost import estimate_cost
except ImportError:

    def estimate_cost(
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_creation_tokens: int,
    ) -> float:
        return (
            input_tokens * 3.0
            + output_tokens * 15.0
            + cache_read_tokens * 0.30
            + cache_creation_tokens * 3.75
        ) / 1_000_000


def parse_jsonl_file(file_path: Path) -> list[ParsedSession]:
    """Parse a single JSONL file. Returns list of sessions (usually 1)."""
    file_path = Path(file_path)
    messages_by_session: dict[str, list[ParsedMessage]] = defaultdict(list)

    with open(file_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            # Skip file-history-snapshot lines
            if msg_type == "file-history-snapshot":
                continue

            session_id = data.get("sessionId")
            if not session_id:
                continue

            uuid = data.get("uuid", "")
            parent_uuid = data.get("parentUuid")

            timestamp_str = data.get("timestamp", "")
            timestamp = _parse_timestamp(timestamp_str)

            message_data = data.get("message")

            # Handle lines without a message field (e.g. compact_boundary)
            if message_data is None:
                role = "system"
                parsed = ParsedMessage(
                    uuid=uuid,
                    parent_uuid=parent_uuid,
                    session_id=session_id,
                    timestamp=timestamp,
                    role=role,
                    type=msg_type,
                    input_tokens=0,
                    output_tokens=0,
                    cache_read_tokens=0,
                    cache_creation_tokens=0,
                    content_length=len(data.get("content", "") or ""),
                )
                messages_by_session[session_id].append(parsed)
                continue

            # Extract role
            role = message_data.get("role", "")
            if not role:
                if msg_type in ("system", "progress"):
                    role = msg_type
                else:
                    role = msg_type

            # Extract token usage
            usage = message_data.get("usage", {}) or {}
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
            cache_read_tokens = usage.get("cache_read_input_tokens", 0)

            # Extract tool calls and content_length from content
            tool_calls: list[ToolCall] = []
            content_length = 0
            content = message_data.get("content")

            if isinstance(content, str):
                content_length = len(content)
            elif isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = item.get("type", "")
                    if item_type == "text":
                        content_length += len(item.get("text", ""))
                    elif item_type == "tool_use":
                        tool_name = item.get("name", "")
                        tool_input = item.get("input", {}) or {}
                        tool_file_path = tool_input.get("file_path")
                        tool_calls.append(
                            ToolCall(
                                tool_name=tool_name,
                                file_path=tool_file_path,
                                timestamp=timestamp,
                            )
                        )

            parsed = ParsedMessage(
                uuid=uuid,
                parent_uuid=parent_uuid,
                session_id=session_id,
                timestamp=timestamp,
                role=role,
                type=msg_type,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
                content_length=content_length,
                tool_calls=tool_calls,
            )
            messages_by_session[session_id].append(parsed)

    # Derive project info from file path's parent directory name
    project_path = file_path.parent.name
    project_name = _derive_project_name(project_path)

    # Build ParsedSession objects
    sessions: list[ParsedSession] = []
    for session_id, messages in messages_by_session.items():
        messages.sort(key=lambda m: m.timestamp)

        started_at = messages[0].timestamp
        ended_at = messages[-1].timestamp
        duration_seconds = int((ended_at - started_at).total_seconds())

        total_input = sum(m.input_tokens for m in messages)
        total_output = sum(m.output_tokens for m in messages)
        total_cache_read = sum(m.cache_read_tokens for m in messages)
        total_cache_creation = sum(m.cache_creation_tokens for m in messages)

        user_count = sum(1 for m in messages if m.role == "user")
        assistant_count = sum(1 for m in messages if m.role == "assistant")

        all_tool_calls = [tc for m in messages for tc in m.tool_calls]
        tool_call_count = len(all_tool_calls)

        file_read_count = sum(1 for tc in all_tool_calls if tc.tool_name == "Read")
        file_write_count = sum(1 for tc in all_tool_calls if tc.tool_name == "Write")
        file_edit_count = sum(1 for tc in all_tool_calls if tc.tool_name == "Edit")
        bash_count = sum(1 for tc in all_tool_calls if tc.tool_name == "Bash")

        cost = estimate_cost(
            total_input, total_output, total_cache_read, total_cache_creation,
        )

        session = ParsedSession(
            session_id=session_id,
            project_path=project_path,
            project_name=project_name,
            source_file=str(file_path),
            started_at=started_at,
            ended_at=ended_at,
            messages=messages,
            duration_seconds=duration_seconds,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_cache_read_tokens=total_cache_read,
            total_cache_creation_tokens=total_cache_creation,
            estimated_cost_usd=cost,
            message_count=len(messages),
            user_message_count=user_count,
            assistant_message_count=assistant_count,
            tool_call_count=tool_call_count,
            file_read_count=file_read_count,
            file_write_count=file_write_count,
            file_edit_count=file_edit_count,
            bash_count=bash_count,
        )
        sessions.append(session)

    return sessions


def discover_jsonl_files(log_dir: Path) -> list[Path]:
    """Find all JSONL files in the log directory, excluding subagent logs."""
    log_dir = Path(log_dir)
    results: list[Path] = []
    for jsonl_file in log_dir.rglob("*.jsonl"):
        # Exclude files inside subagents/ subdirectories
        if "subagents" in jsonl_file.parts:
            continue
        results.append(jsonl_file)
    results.sort()
    return results


def _parse_timestamp(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp string into a datetime."""
    if not ts:
        return datetime.now(tz=timezone.utc)
    # Handle Z suffix
    ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts)


def _derive_project_name(project_path: str) -> str:
    """Derive a human-friendly project name from a Claude project directory name.

    e.g. '-Users-brianliou-projects-slopfarm' -> 'slopfarm'
    """
    marker = "-projects-"
    idx = project_path.rfind(marker)
    if idx != -1:
        return project_path[idx + len(marker) :]
    # Fallback: use last segment after the last dash
    return project_path.rsplit("-", 1)[-1] if "-" in project_path else project_path
