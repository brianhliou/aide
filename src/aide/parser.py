"""JSONL parser — reads Claude Code session logs into ParsedSession objects."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from aide.models import ParsedMessage, ParsedSession, ToolCall, WorkBlock

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
    # Per-session metadata collected from special line types
    session_titles: dict[str, str] = {}
    session_turn_durations: dict[str, list[int]] = defaultdict(list)
    session_git_branches: dict[str, str] = {}
    # Track tool_use_ids that had errors (from tool_result in user messages)
    error_tool_use_ids: set[str] = set()
    # Track all ToolCalls by tool_use_id for error correlation
    tool_use_id_map: dict[str, ToolCall] = {}
    # Track compact_boundary preTokens per session
    session_compact_pre_tokens: dict[str, list[int]] = defaultdict(list)
    # Track permission modes per session
    session_permission_modes: dict[str, list[str]] = defaultdict(list)

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

            # Handle custom-title lines
            if msg_type == "custom-title":
                title = data.get("customTitle", "")
                if title:
                    session_titles[session_id] = title
                continue

            # Handle turn_duration system lines
            subtype = data.get("subtype", "")
            if subtype == "turn_duration":
                duration_ms = data.get("durationMs", 0)
                if duration_ms > 0:
                    session_turn_durations[session_id].append(duration_ms)
                continue

            uuid = data.get("uuid", "")
            parent_uuid = data.get("parentUuid")

            timestamp_str = data.get("timestamp", "")
            timestamp = _parse_timestamp(timestamp_str)

            # Extract git branch from first user message per session
            if msg_type == "user" and session_id not in session_git_branches:
                git_branch = data.get("gitBranch")
                if git_branch:
                    session_git_branches[session_id] = git_branch

            # Extract permission mode from user messages
            if msg_type == "user":
                perm_mode = data.get("permissionMode")
                if perm_mode:
                    session_permission_modes[session_id].append(perm_mode)

            message_data = data.get("message")

            # Handle lines without a message field (e.g. compact_boundary)
            if message_data is None:
                role = "system"
                # Extract compaction metadata if present
                if subtype == "compact_boundary":
                    compact_meta = data.get("compactMetadata", {}) or {}
                    pre_tokens = compact_meta.get("preTokens")
                    if pre_tokens and isinstance(pre_tokens, int):
                        session_compact_pre_tokens[session_id].append(pre_tokens)
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

            # Extract model and stop_reason from assistant messages
            model = None
            stop_reason = None
            if role == "assistant":
                model = message_data.get("model")
                stop_reason = message_data.get("stop_reason")

            # Extract tool calls, content_length, prompt_length, thinking, and error ids
            tool_calls: list[ToolCall] = []
            content_length = 0
            prompt_length = 0
            thinking_chars = 0
            content = message_data.get("content")

            if isinstance(content, str):
                content_length = len(content)
                if role == "user":
                    prompt_length = content_length
            elif isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = item.get("type", "")
                    if item_type == "text":
                        text_len = len(item.get("text", ""))
                        content_length += text_len
                        if role == "user":
                            prompt_length += text_len
                    elif item_type == "tool_use":
                        tool_name = item.get("name", "")
                        tool_input = item.get("input", {}) or {}
                        tool_file_path = tool_input.get("file_path")
                        tool_use_id = item.get("id")

                        # Extract Bash-specific fields
                        command = None
                        description = None
                        if tool_name == "Bash":
                            command = tool_input.get("command")
                            description = tool_input.get("description")

                        # Extract edit/write size fields
                        old_string_len = None
                        new_string_len = None
                        if tool_name == "Edit":
                            old_str = tool_input.get("old_string")
                            new_str = tool_input.get("new_string")
                            if old_str is not None:
                                old_string_len = len(old_str)
                            if new_str is not None:
                                new_string_len = len(new_str)
                        elif tool_name == "Write":
                            write_content = tool_input.get("content")
                            if write_content is not None:
                                new_string_len = len(write_content)

                        tc = ToolCall(
                            tool_name=tool_name,
                            file_path=tool_file_path,
                            timestamp=timestamp,
                            tool_use_id=tool_use_id,
                            command=command,
                            description=description,
                            old_string_len=old_string_len,
                            new_string_len=new_string_len,
                        )
                        tool_calls.append(tc)
                        if tool_use_id:
                            tool_use_id_map[tool_use_id] = tc
                    elif item_type == "thinking":
                        thinking_text = item.get("thinking", "")
                        if thinking_text:
                            thinking_chars += len(thinking_text)
                    elif item_type == "tool_result":
                        # Check for errors in tool results
                        if item.get("is_error"):
                            result_tool_use_id = item.get("tool_use_id")
                            if result_tool_use_id:
                                error_tool_use_ids.add(result_tool_use_id)

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
                model=model,
                stop_reason=stop_reason,
                prompt_length=prompt_length,
                thinking_chars=thinking_chars,
            )
            messages_by_session[session_id].append(parsed)

    # Correlate errors: mark ToolCalls whose tool_use_id appeared in error results
    for err_id in error_tool_use_ids:
        if err_id in tool_use_id_map:
            tool_use_id_map[err_id].is_error = True

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

        # Compute compaction data
        # Prefer compact_boundary metadata when available
        pre_tokens_list = session_compact_pre_tokens.get(session_id, [])
        if pre_tokens_list:
            compaction_count = len(pre_tokens_list)
            peak_context = max(pre_tokens_list)
            # Also check assistant message context sizes for a potentially higher peak
            context_sizes = [
                m.input_tokens + m.cache_read_tokens + m.cache_creation_tokens
                for m in messages
                if m.role == "assistant"
            ]
            if context_sizes:
                peak_context = max(peak_context, max(context_sizes))
        else:
            # Fallback: heuristic from assistant message context drops
            context_sizes = [
                m.input_tokens + m.cache_read_tokens + m.cache_creation_tokens
                for m in messages
                if m.role == "assistant"
            ]
            peak_context = max(context_sizes) if context_sizes else 0
            compaction_count = 0
            for i in range(1, len(context_sizes)):
                if context_sizes[i - 1] > 100_000 and context_sizes[i] < context_sizes[i - 1] * 0.5:
                    compaction_count += 1

        # Turn duration metrics
        turn_durations = session_turn_durations.get(session_id, [])
        total_turn_duration_ms = sum(turn_durations)
        turn_count = len(turn_durations)
        max_turn_duration_ms = max(turn_durations) if turn_durations else 0

        # Tool error count
        tool_error_count = sum(1 for tc in all_tool_calls if tc.is_error)

        # Git branch
        git_branch = session_git_branches.get(session_id)

        # Custom title
        custom_title = session_titles.get(session_id)

        # Derived: rework_file_count (files edited 3+ times)
        file_edit_counts: dict[str, int] = defaultdict(int)
        for tc in all_tool_calls:
            if tc.tool_name in ("Edit", "Write") and tc.file_path:
                file_edit_counts[tc.file_path] += 1
        rework_file_count = sum(1 for c in file_edit_counts.values() if c >= 3)

        # Derived: test_after_edit_rate
        # Within each assistant message, if Edit/Write is followed by Bash, the edit is "tested"
        total_edits = 0
        tested_edits = 0
        for m in messages:
            if m.role != "assistant" or not m.tool_calls:
                continue
            for i, tc in enumerate(m.tool_calls):
                if tc.tool_name in ("Edit", "Write"):
                    total_edits += 1
                    # Check if any subsequent tool call in this message is Bash
                    for j in range(i + 1, len(m.tool_calls)):
                        if m.tool_calls[j].tool_name == "Bash":
                            tested_edits += 1
                            break
        test_after_edit_rate = tested_edits / total_edits if total_edits > 0 else 0.0

        # Thinking metrics
        total_thinking_chars = sum(m.thinking_chars for m in messages)
        thinking_message_count = sum(1 for m in messages if m.thinking_chars > 0)

        # Permission mode — most common mode in this session
        perm_modes = session_permission_modes.get(session_id, [])
        if perm_modes:
            from collections import Counter as _Counter
            permission_mode = _Counter(perm_modes).most_common(1)[0][0]
        else:
            permission_mode = None

        # Work blocks
        work_blocks = _compute_work_blocks(session_id, messages)
        active_duration_seconds = sum(
            b.duration_seconds for b in work_blocks
        )

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
            compaction_count=compaction_count,
            peak_context_tokens=peak_context,
            custom_title=custom_title,
            total_turn_duration_ms=total_turn_duration_ms,
            turn_count=turn_count,
            max_turn_duration_ms=max_turn_duration_ms,
            tool_error_count=tool_error_count,
            git_branch=git_branch,
            rework_file_count=rework_file_count,
            test_after_edit_rate=round(test_after_edit_rate, 4),
            total_thinking_chars=total_thinking_chars,
            thinking_message_count=thinking_message_count,
            permission_mode=permission_mode,
            active_duration_seconds=active_duration_seconds,
            work_blocks=work_blocks,
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


IDLE_GAP_SECONDS = 30 * 60  # 30 minutes


def _compute_work_blocks(
    session_id: str, messages: list[ParsedMessage],
) -> list[WorkBlock]:
    """Split a session into work blocks at idle gaps > 30 minutes."""
    if not messages:
        return []

    blocks: list[WorkBlock] = []
    block_start = messages[0].timestamp
    block_msgs = 1
    prev_ts = messages[0].timestamp

    for msg in messages[1:]:
        gap = (msg.timestamp - prev_ts).total_seconds()
        if gap > IDLE_GAP_SECONDS:
            blocks.append(WorkBlock(
                session_id=session_id,
                block_index=len(blocks),
                started_at=block_start,
                ended_at=prev_ts,
                duration_seconds=int(
                    (prev_ts - block_start).total_seconds(),
                ),
                message_count=block_msgs,
            ))
            block_start = msg.timestamp
            block_msgs = 0
        block_msgs += 1
        prev_ts = msg.timestamp

    # Final block
    blocks.append(WorkBlock(
        session_id=session_id,
        block_index=len(blocks),
        started_at=block_start,
        ended_at=prev_ts,
        duration_seconds=int((prev_ts - block_start).total_seconds()),
        message_count=block_msgs,
    ))

    return blocks


def _parse_timestamp(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp string into a datetime."""
    if not ts:
        return datetime.now(tz=timezone.utc)
    # Handle Z suffix
    ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts)


def _derive_project_name(project_path: str) -> str:
    """Derive a human-friendly project name from a Claude project directory name.

    e.g. '-Users-username-projects-myproject' -> 'myproject'
    """
    marker = "-projects-"
    idx = project_path.rfind(marker)
    if idx != -1:
        return project_path[idx + len(marker) :]
    # Fallback: use last segment after the last dash
    return project_path.rsplit("-", 1)[-1] if "-" in project_path else project_path
