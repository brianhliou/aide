"""Parser for Codex JSONL rollout logs.

Codex and Claude Code use different JSONL shapes. This module maps Codex
events into aide's existing ParsedSession contract so the current dashboard can
surface command usage, approval friction, and token volume without a schema
migration.
"""

from __future__ import annotations

import json
import re
import shlex
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aide.cost import estimate_cost
from aide.models import ParsedMessage, ParsedSession, ToolCall
from aide.parser import _compute_work_blocks, _parse_timestamp


def discover_codex_jsonl_files(log_dir: Path) -> list[Path]:
    """Find Codex session JSONL files."""
    log_dir = Path(log_dir)
    if not log_dir.exists():
        return []
    results = sorted(log_dir.rglob("*.jsonl"))
    return [path for path in results if "subagents" not in path.parts]


def parse_codex_jsonl_file(file_path: Path) -> list[ParsedSession]:
    """Parse a single Codex rollout JSONL file into one ParsedSession."""
    file_path = Path(file_path)

    session_id = file_path.stem
    cwd: str | None = None
    started_at: datetime | None = None
    messages: list[ParsedMessage] = []
    tool_calls_by_id: dict[str, ToolCall] = {}
    token_totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 0,
    }
    peak_context_tokens = 0
    approval_policies: list[str] = []
    sandbox_policies: list[str] = []
    project_candidates: Counter[str] = Counter()
    model: str | None = None

    with open(file_path) as f:
        for index, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            timestamp = _parse_timestamp(data.get("timestamp", ""))
            started_at = started_at or timestamp
            payload = data.get("payload") or {}
            if not isinstance(payload, dict):
                continue

            payload_type = payload.get("type") or data.get("type", "")

            if data.get("type") == "session_meta":
                session_id = payload.get("id") or session_id
                cwd = payload.get("cwd") or cwd
                _add_project_candidate(project_candidates, payload.get("cwd"))
                model = payload.get("model") or model
                continue

            if payload_type == "turn_context":
                cwd = payload.get("cwd") or cwd
                _add_project_candidate(project_candidates, payload.get("cwd"))
                model = payload.get("model") or model
                approval = payload.get("approval_policy")
                sandbox = payload.get("sandbox_policy")
                if approval:
                    approval_policies.append(str(approval))
                if sandbox:
                    sandbox_policies.append(_policy_name(sandbox))
                continue

            if payload_type == "token_count":
                info = payload.get("info") or {}
                total_usage = info.get("total_token_usage") or {}
                if isinstance(total_usage, dict):
                    token_totals = {
                        "input_tokens": _int(total_usage.get("input_tokens")),
                        "output_tokens": _int(total_usage.get("output_tokens")),
                        "cached_input_tokens": _int(total_usage.get("cached_input_tokens")),
                        "reasoning_output_tokens": _int(
                            total_usage.get("reasoning_output_tokens")
                        ),
                        "total_tokens": _int(total_usage.get("total_tokens")),
                    }
                    peak_context_tokens = max(
                        peak_context_tokens,
                        _int(info.get("model_context_window")),
                        token_totals["total_tokens"],
                    )
                continue

            if payload_type in {"user_message", "agent_message", "message"}:
                role = _message_role(payload_type, payload)
                content_length = _content_length(payload)
                messages.append(
                    ParsedMessage(
                        uuid=f"{file_path.stem}:{index}",
                        parent_uuid=None,
                        session_id=session_id,
                        timestamp=timestamp,
                        role=role,
                        type=payload_type,
                        input_tokens=0,
                        output_tokens=0,
                        cache_read_tokens=0,
                        cache_creation_tokens=0,
                        model=model,
                        content_length=content_length,
                        prompt_length=content_length if role == "user" else 0,
                    )
                )
                continue

            if payload_type == "function_call":
                args = _json_object(payload.get("arguments"))
                cwd = cwd or _workdir_from_args(args)
                _add_project_candidate(project_candidates, _workdir_from_args(args))
                tc = _tool_call_from_function_payload(payload, timestamp, args=args)
                if tc is None:
                    continue
                _add_project_candidate(project_candidates, tc.file_path)
                _add_command_project_candidates(project_candidates, tc.command)
                tool_calls_by_id[payload.get("call_id", f"{index}")] = tc
                messages.append(
                    ParsedMessage(
                        uuid=f"{file_path.stem}:{index}",
                        parent_uuid=None,
                        session_id=session_id,
                        timestamp=timestamp,
                        role="assistant",
                        type="function_call",
                        input_tokens=0,
                        output_tokens=0,
                        cache_read_tokens=0,
                        cache_creation_tokens=0,
                        model=model,
                        content_length=0,
                        tool_calls=[tc],
                    )
                )
                continue

            if payload_type == "function_call_output":
                call_id = payload.get("call_id")
                output = payload.get("output")
                if call_id in tool_calls_by_id and _output_looks_error(output):
                    tool_calls_by_id[call_id].is_error = True
                continue

            if payload_type == "exec_command_end":
                cwd = cwd or payload.get("cwd")
                _add_project_candidate(project_candidates, payload.get("cwd"))
                call_id = payload.get("call_id")
                if call_id in tool_calls_by_id:
                    tc = tool_calls_by_id[call_id]
                    exit_code = payload.get("exit_code")
                    tc.is_error = exit_code not in (0, None)
                    if tc.file_path is None:
                        tc.file_path = _file_path_from_parsed_cmd(payload.get("parsed_cmd"))
                    _apply_parsed_cmd_semantics(tc, payload.get("parsed_cmd"))
                    _add_project_candidate(project_candidates, tc.file_path)
                continue

    if not messages:
        return []

    messages.sort(key=lambda m: m.timestamp)
    started = messages[0].timestamp if messages else started_at or _now()
    ended = messages[-1].timestamp if messages else started
    duration_seconds = int((ended - started).total_seconds())
    all_tool_calls = [tc for m in messages for tc in m.tool_calls]
    project_path = _choose_codex_project_path(
        cwd or str(file_path.parent),
        project_candidates,
    )
    project_name = _derive_codex_project_name(project_path)
    work_blocks = _compute_work_blocks(session_id, messages)
    cost = estimate_cost(
        token_totals["input_tokens"],
        token_totals["output_tokens"],
        cache_read_tokens=token_totals["cached_input_tokens"],
        model=model,
        provider="codex",
    )

    session = ParsedSession(
        session_id=session_id,
        project_path=project_path,
        project_name=project_name,
        source_file=str(file_path),
        started_at=started,
        ended_at=ended,
        messages=messages,
        duration_seconds=duration_seconds,
        total_input_tokens=token_totals["input_tokens"],
        total_output_tokens=token_totals["output_tokens"],
        total_cache_read_tokens=token_totals["cached_input_tokens"],
        total_cache_creation_tokens=0,
        estimated_cost_usd=cost,
        message_count=len(messages),
        user_message_count=sum(1 for m in messages if m.role == "user"),
        assistant_message_count=sum(1 for m in messages if m.role == "assistant"),
        tool_call_count=len(all_tool_calls),
        file_read_count=sum(1 for tc in all_tool_calls if tc.tool_name == "Read"),
        file_write_count=sum(1 for tc in all_tool_calls if tc.tool_name == "Write"),
        file_edit_count=sum(1 for tc in all_tool_calls if tc.tool_name == "Edit"),
        bash_count=sum(1 for tc in all_tool_calls if tc.tool_name == "Bash"),
        compaction_count=0,
        peak_context_tokens=peak_context_tokens,
        custom_title=None,
        total_turn_duration_ms=0,
        turn_count=0,
        max_turn_duration_ms=0,
        tool_error_count=sum(1 for tc in all_tool_calls if tc.is_error),
        git_branch=None,
        rework_file_count=_rework_file_count(all_tool_calls),
        test_after_edit_rate=0.0,
        total_thinking_chars=0,
        thinking_message_count=0,
        permission_mode=_permission_mode(approval_policies, sandbox_policies),
        active_duration_seconds=sum(b.duration_seconds for b in work_blocks),
        work_blocks=work_blocks,
        provider="codex",
    )
    return [session]


def _tool_call_from_function_payload(
    payload: dict[str, Any],
    timestamp: datetime,
    args: dict[str, Any] | None = None,
) -> ToolCall | None:
    name = str(payload.get("name") or "")
    args = args if args is not None else _json_object(payload.get("arguments"))
    command = _command_from_args(name, args)
    tool_name = _codex_tool_name(name, command)
    if tool_name is None:
        return None

    description = args.get("justification") or _permission_description(args)
    return ToolCall(
        tool_name=tool_name,
        file_path=_file_path_from_command(command) if tool_name == "Edit" else None,
        timestamp=timestamp,
        tool_use_id=payload.get("call_id"),
        command=command,
        description=description,
        is_error=False,
    )


def _codex_tool_name(name: str, command: str | None = None) -> str | None:
    if name in {"exec_command", "shell"}:
        if _command_looks_file_mutation(command):
            return "Edit"
        return "Bash"
    if name == "apply_patch":
        return "Edit"
    if name == "write_stdin":
        return "Bash"
    if name == "view_image":
        return "Read"
    if name in {"update_plan", "list_mcp_resources", "list_mcp_resource_templates"}:
        return name
    return name or None


def _command_from_args(name: str, args: dict[str, Any]) -> str | None:
    if name == "exec_command":
        cmd = args.get("cmd")
        return str(cmd) if cmd is not None else None
    if name == "shell":
        command = args.get("command")
        return str(command) if command is not None else None
    if name == "write_stdin":
        target = args.get("session_id")
        return f"write_stdin {target}" if target is not None else "write_stdin"
    return None


def _permission_description(args: dict[str, Any]) -> str | None:
    sandbox_permissions = args.get("sandbox_permissions")
    prefix_rule = args.get("prefix_rule")
    if not sandbox_permissions and not prefix_rule:
        return None
    parts = []
    if sandbox_permissions:
        parts.append(f"sandbox_permissions={sandbox_permissions}")
    if prefix_rule:
        parts.append(f"prefix_rule={prefix_rule}")
    return "; ".join(parts)


def _message_role(payload_type: str, payload: dict[str, Any]) -> str:
    if payload_type == "user_message":
        return "user"
    if payload_type == "agent_message":
        return "assistant"
    role = payload.get("role")
    return str(role) if role else "system"


def _content_length(payload: dict[str, Any]) -> int:
    total = 0
    message = payload.get("message")
    if isinstance(message, str):
        total += len(message)
    for element in payload.get("text_elements") or []:
        if isinstance(element, str):
            total += len(element)
        elif isinstance(element, dict):
            text = element.get("text")
            if isinstance(text, str):
                total += len(text)
    content = payload.get("content")
    if isinstance(content, str):
        total += len(content)
    elif isinstance(content, list):
        total += sum(_content_item_length(item) for item in content)
    return total


def _content_item_length(item: Any) -> int:
    if isinstance(item, str):
        return len(item)
    if not isinstance(item, dict):
        return 0
    for key in ("text", "input_text", "output_text"):
        value = item.get(key)
        if isinstance(value, str):
            return len(value)
    return 0


def _file_path_from_parsed_cmd(parsed_cmd: Any) -> str | None:
    if not isinstance(parsed_cmd, list):
        return None
    for item in parsed_cmd:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if path:
            return str(path)
    return None


def _apply_parsed_cmd_semantics(tc: ToolCall, parsed_cmd: Any) -> None:
    """Refine tool semantics from Codex's parsed command metadata."""
    if not isinstance(parsed_cmd, list):
        return
    for item in parsed_cmd:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in {"edit", "write"}:
            tc.tool_name = "Edit" if item_type == "edit" else "Write"
            if item.get("path"):
                tc.file_path = str(item["path"])
            return


def _command_looks_file_mutation(command: str | None) -> bool:
    if not command:
        return False
    lowered = command.lower()
    return (
        "apply_patch" in lowered
        or "*** update file:" in lowered
        or "*** add file:" in lowered
        or "*** delete file:" in lowered
        or re.search(r"(^|\s)(sed\s+-i|perl\s+-p?i)\b", lowered) is not None
        or re.search(r"(>>?|tee\s+(-a\s+)?)\s*[\w./~:@+-]+", lowered) is not None
    )


def _file_path_from_command(command: str | None) -> str | None:
    if not command:
        return None

    patch_match = re.search(
        r"^\*\*\* (?:Update|Add|Delete) File: (.+)$",
        command,
        flags=re.MULTILINE,
    )
    if patch_match:
        return patch_match.group(1).strip()

    redirect_match = re.search(r">>?\s*([^\s;&|]+)", command)
    if redirect_match:
        return redirect_match.group(1).strip("'\"")

    tee_match = re.search(r"\btee\s+(?:-a\s+)?([^\s;&|]+)", command)
    if tee_match:
        return tee_match.group(1).strip("'\"")

    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if not parts:
        return None
    command_name = Path(parts[0]).name
    if command_name in {"sed", "perl"} and any(part.startswith("-i") for part in parts):
        for part in reversed(parts[1:]):
            if (not part.startswith("-")) and ("/" in part or "." in part):
                return part
    return None


def _workdir_from_args(args: dict[str, Any]) -> str | None:
    workdir = args.get("workdir")
    return str(workdir) if workdir else None


def _policy_name(value: Any) -> str:
    if isinstance(value, dict):
        name = value.get("mode") or value.get("type") or value.get("policy")
        return str(name) if name else "unknown"
    return str(value)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _output_looks_error(output: Any) -> bool:
    if not isinstance(output, str):
        return False
    lowered = output.lower()
    return "error" in lowered or "permission denied" in lowered


def _choose_codex_project_path(
    fallback_path: str,
    candidates: Counter[str],
) -> str:
    """Choose a specific project root when Codex reports a generic parent cwd."""
    fallback_root = _project_root_from_path(fallback_path)
    if fallback_root and not _is_ambiguous_codex_project_path(fallback_path):
        return fallback_root

    if candidates:
        return candidates.most_common(1)[0][0]
    return fallback_path


def _add_project_candidate(candidates: Counter[str], value: Any) -> None:
    root = _project_root_from_path(value)
    if root:
        candidates[root] += 1


def _add_command_project_candidates(
    candidates: Counter[str],
    command: str | None,
) -> None:
    if not command:
        return

    for match in re.finditer(r"/[^\s\"';&|]+", command):
        _add_project_candidate(candidates, match.group(0))

    try:
        parts = shlex.split(command)
    except ValueError:
        return
    for part in parts:
        _add_project_candidate(candidates, part)


def _project_root_from_path(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    if not text.startswith("/") and not text.startswith("~"):
        return None

    try:
        path = Path(text).expanduser() if text.startswith("~") else Path(text)
    except RuntimeError:
        return None

    parts = path.parts
    for marker in ("projects", "a-projects"):
        if marker not in parts:
            continue
        idx = len(parts) - 1 - list(reversed(parts)).index(marker)
        if idx + 1 >= len(parts):
            return None
        project = parts[idx + 1]
        if project in {"<project>", "<path>"}:
            return None
        if idx + 2 == len(parts) and Path(project).suffix:
            return None
        return str(Path(*parts[:idx + 2]))
    return None


def _is_ambiguous_codex_project_path(project_path: str) -> bool:
    path = Path(project_path)
    name = path.name
    if name in {"projects", "a-projects", "sessions"}:
        return True
    parts = path.parts
    return ".codex" in parts and "sessions" in parts


def _derive_codex_project_name(project_path: str) -> str:
    path = Path(project_path)
    parts = path.parts
    if "projects" in parts:
        idx = len(parts) - 1 - list(reversed(parts)).index("projects")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    if ".codex" in parts and "sessions" in parts:
        return "codex"
    return path.name or "codex"


def _rework_file_count(tool_calls: list[ToolCall]) -> int:
    edit_counts: dict[str, int] = defaultdict(int)
    for tc in tool_calls:
        if tc.tool_name in {"Edit", "Write"} and tc.file_path:
            edit_counts[tc.file_path] += 1
    return sum(1 for count in edit_counts.values() if count >= 3)


def _permission_mode(
    approval_policies: list[str], sandbox_policies: list[str],
) -> str | None:
    if not approval_policies and not sandbox_policies:
        return None
    approval = approval_policies[-1] if approval_policies else "unknown"
    sandbox = sandbox_policies[-1] if sandbox_policies else "unknown"
    return f"{approval}/{sandbox}"


def _int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)
