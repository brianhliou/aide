"""Tests for Codex JSONL parsing."""

from __future__ import annotations

import json
from pathlib import Path

from aide.codex_parser import discover_codex_jsonl_files, parse_codex_jsonl_file

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_parse_codex_session_extracts_commands_and_permissions(tmp_path):
    session_dir = tmp_path / "2026" / "05" / "01"
    session_dir.mkdir(parents=True)
    jsonl = session_dir / "rollout-2026-05-01T00-00-00-test.jsonl"
    lines = [
        {
            "timestamp": "2026-05-01T00:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": "codex-session-1",
                "cwd": "/Users/brianliou/projects/aide",
            },
        },
        {
            "timestamp": "2026-05-01T00:00:01Z",
            "type": "event_msg",
            "payload": {
                "type": "turn_context",
                "model": "gpt-5.5",
                "approval_policy": "on-request",
                "sandbox_policy": "workspace-write",
                "cwd": "/Users/brianliou/projects/aide",
            },
        },
        {
            "timestamp": "2026-05-01T00:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "user_message",
                "message": "scan codex logs",
                "text_elements": ["scan codex logs"],
                "images": [],
                "local_images": [],
            },
        },
        {
            "timestamp": "2026-05-01T00:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-1",
                "arguments": json.dumps(
                    {
                        "cmd": "npm --workspace apps/web run dev -- -p 3017",
                        "sandbox_permissions": "require_escalated",
                        "justification": "Allow starting the local dev server?",
                        "prefix_rule": ["npm", "--workspace", "apps/web", "run", "dev"],
                    }
                ),
            },
        },
        {
            "timestamp": "2026-05-01T00:00:04Z",
            "type": "event_msg",
            "payload": {
                "type": "exec_command_end",
                "call_id": "call-1",
                "exit_code": 0,
                "status": "completed",
                "parsed_cmd": [{"type": "unknown", "cmd": "npm --workspace apps/web run dev"}],
            },
        },
        {
            "timestamp": "2026-05-01T00:00:05Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 1200,
                        "cached_input_tokens": 800,
                        "output_tokens": 200,
                        "reasoning_output_tokens": 50,
                        "total_tokens": 1400,
                    },
                    "model_context_window": 258400,
                },
            },
        },
    ]
    jsonl.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    session = parse_codex_jsonl_file(jsonl)[0]

    assert session.session_id == "codex-session-1"
    assert session.project_name == "aide"
    assert session.user_message_count == 1
    assert session.tool_call_count == 1
    assert session.bash_count == 1
    assert session.permission_mode == "on-request/workspace-write"
    assert session.total_input_tokens == 1200
    assert session.total_cache_read_tokens == 800
    assert session.total_output_tokens == 200
    assert session.peak_context_tokens == 258400
    assert session.estimated_cost_usd == 0.0084

    tool_call = session.messages[1].tool_calls[0]
    assert tool_call.tool_name == "Bash"
    assert tool_call.command == "npm --workspace apps/web run dev -- -p 3017"
    assert tool_call.description == "Allow starting the local dev server?"
    assert tool_call.is_error is False


def test_codex_exec_nonzero_exit_marks_tool_error(tmp_path):
    jsonl = tmp_path / "rollout.jsonl"
    lines = [
        {
            "timestamp": "2026-05-01T00:00:00Z",
            "type": "session_meta",
            "payload": {"id": "codex-session-2", "cwd": "/tmp/project"},
        },
        {
            "timestamp": "2026-05-01T00:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-err",
                "arguments": json.dumps({"cmd": "npm run build"}),
            },
        },
        {
            "timestamp": "2026-05-01T00:00:02Z",
            "type": "event_msg",
            "payload": {
                "type": "exec_command_end",
                "call_id": "call-err",
                "exit_code": 1,
                "status": "failed",
            },
        },
    ]
    jsonl.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    session = parse_codex_jsonl_file(jsonl)[0]

    assert session.tool_error_count == 1
    assert session.messages[0].tool_calls[0].is_error is True


def test_codex_uses_command_workdir_as_project_fallback(tmp_path):
    jsonl = tmp_path / "2025" / "09" / "03" / "rollout.jsonl"
    jsonl.parent.mkdir(parents=True)
    lines = [
        {
            "timestamp": "2026-05-01T00:00:00Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-workdir",
                "arguments": json.dumps({
                    "cmd": "pwd",
                    "workdir": "/Users/brianliou/projects/aide",
                }),
            },
        },
    ]
    jsonl.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    session = parse_codex_jsonl_file(jsonl)[0]

    assert session.project_path == "/Users/brianliou/projects/aide"
    assert session.project_name == "aide"


def test_codex_refines_parent_projects_cwd_from_later_workdir(tmp_path):
    jsonl = tmp_path / "rollout.jsonl"
    lines = [
        {
            "timestamp": "2026-05-01T00:00:00Z",
            "type": "session_meta",
            "payload": {"id": "parent-cwd", "cwd": "/Users/brianliou/projects"},
        },
        {
            "timestamp": "2026-05-01T00:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-workdir",
                "arguments": json.dumps({
                    "cmd": "uv run pytest",
                    "workdir": "/Users/brianliou/projects/aide",
                }),
            },
        },
    ]
    jsonl.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    session = parse_codex_jsonl_file(jsonl)[0]

    assert session.project_path == "/Users/brianliou/projects/aide"
    assert session.project_name == "aide"


def test_codex_refines_parent_projects_cwd_from_absolute_command_path(tmp_path):
    jsonl = tmp_path / "rollout.jsonl"
    lines = [
        {
            "timestamp": "2026-05-01T00:00:00Z",
            "type": "session_meta",
            "payload": {"id": "absolute-path", "cwd": "/Users/brianliou/projects"},
        },
        {
            "timestamp": "2026-05-01T00:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-absolute-path",
                "arguments": json.dumps({
                    "cmd": "rg Permission /Users/brianliou/projects/aide/src/aide",
                }),
            },
        },
    ]
    jsonl.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    session = parse_codex_jsonl_file(jsonl)[0]

    assert session.project_path == "/Users/brianliou/projects/aide"
    assert session.project_name == "aide"


def test_codex_does_not_treat_file_under_projects_as_project(tmp_path):
    jsonl = tmp_path / "rollout.jsonl"
    lines = [
        {
            "timestamp": "2026-05-01T00:00:00Z",
            "type": "session_meta",
            "payload": {"id": "direct-file", "cwd": "/Users/brianliou/projects"},
        },
        {
            "timestamp": "2026-05-01T00:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-direct-file",
                "arguments": json.dumps({
                    "cmd": "sed -n '1,80p' /Users/brianliou/projects/AGENTS.md",
                }),
            },
        },
    ]
    jsonl.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    session = parse_codex_jsonl_file(jsonl)[0]

    assert session.project_path == "/Users/brianliou/projects"
    assert session.project_name == "projects"


def test_codex_session_log_date_path_falls_back_to_codex_project(tmp_path):
    jsonl = tmp_path / ".codex" / "sessions" / "2025" / "09" / "03" / "rollout.jsonl"
    jsonl.parent.mkdir(parents=True)
    lines = [
        {
            "timestamp": "2026-05-01T00:00:00Z",
            "type": "response_item",
            "payload": {"type": "user_message", "message": "hi"},
        },
    ]
    jsonl.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    session = parse_codex_jsonl_file(jsonl)[0]

    assert session.project_name == "codex"


def test_codex_normalizes_dict_sandbox_policy(tmp_path):
    jsonl = tmp_path / "rollout.jsonl"
    lines = [
        {
            "timestamp": "2026-05-01T00:00:00Z",
            "type": "event_msg",
            "payload": {
                "type": "turn_context",
                "approval_policy": "on-request",
                "sandbox_policy": {
                    "type": "workspace-write",
                    "writable_roots": ["/Users/brianliou/projects/aide"],
                },
                "cwd": "/Users/brianliou/projects/aide",
            },
        },
        {
            "timestamp": "2026-05-01T00:00:01Z",
            "type": "response_item",
            "payload": {"type": "user_message", "message": "hi"},
        },
    ]
    jsonl.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    session = parse_codex_jsonl_file(jsonl)[0]

    assert session.permission_mode == "on-request/workspace-write"


def test_codex_shell_function_is_normalized_to_bash(tmp_path):
    jsonl = tmp_path / "rollout.jsonl"
    lines = [
        {
            "timestamp": "2026-05-01T00:00:00Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "shell",
                "call_id": "call-shell",
                "arguments": json.dumps({
                    "command": "npm test",
                    "workdir": "/Users/brianliou/projects/aide",
                }),
            },
        },
    ]
    jsonl.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    session = parse_codex_jsonl_file(jsonl)[0]

    assert session.bash_count == 1
    assert session.messages[0].tool_calls[0].tool_name == "Bash"
    assert session.messages[0].tool_calls[0].command == "npm test"


def test_codex_shell_file_mutation_counts_as_edit(tmp_path):
    jsonl = tmp_path / "rollout.jsonl"
    lines = [
        {
            "timestamp": "2026-05-01T00:00:00Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-edit",
                "arguments": json.dumps({
                    "cmd": "sed -i '' 's/old/new/' src/app.py",
                    "workdir": "/Users/brianliou/projects/aide",
                }),
            },
        },
    ]
    jsonl.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    session = parse_codex_jsonl_file(jsonl)[0]

    assert session.file_edit_count == 1
    tool_call = session.messages[0].tool_calls[0]
    assert tool_call.tool_name == "Edit"
    assert tool_call.file_path == "src/app.py"


def test_discover_codex_jsonl_files_excludes_subagents(tmp_path):
    (tmp_path / "main.jsonl").touch()
    subagents = tmp_path / "subagents"
    subagents.mkdir()
    (subagents / "agent.jsonl").touch()

    found = discover_codex_jsonl_files(tmp_path)

    assert found == [tmp_path / "main.jsonl"]


def test_parse_redacted_real_codex_fixture():
    sessions = parse_codex_jsonl_file(FIXTURES_DIR / "codex-redacted.jsonl")

    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id
    assert session.message_count > 0
    assert session.tool_call_count > 0
    assert session.estimated_cost_usd > 0
    assert session.source_file.endswith("codex-redacted.jsonl")
