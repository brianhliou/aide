"""Tests for provider log redaction."""

from __future__ import annotations

import json

from aide.redaction import redact_jsonl_file, redact_value


def test_redact_codex_jsonl_preserves_structure_and_redacts_sensitive_fields(tmp_path):
    source = tmp_path / "codex.jsonl"
    out = tmp_path / "redacted.jsonl"
    record = {
        "timestamp": "2026-05-01T00:00:00Z",
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "call-1",
            "arguments": json.dumps(
                {
                    "cmd": (
                        "curl https://example.com/private?token=abc "
                        "-H 'Authorization: Bearer sk-secret' "
                        "/Users/brianliou/projects/aide/app.py"
                    ),
                    "justification": "Need to call the private API for Brian",
                    "env": {"OPENAI_API_KEY": "sk-secret"},
                }
            ),
        },
    }
    source.write_text(json.dumps(record) + "\n")

    result = redact_jsonl_file(source, out, provider="codex")

    assert result.files_processed == 1
    assert result.lines_processed == 1
    assert result.fields_redacted > 0

    redacted = json.loads(out.read_text())
    assert redacted["timestamp"] == "2026-05-01T00:00:00Z"
    assert redacted["payload"]["type"] == "function_call"
    assert redacted["payload"]["call_id"] == "call-1"

    args = json.loads(redacted["payload"]["arguments"])
    assert "curl" in args["cmd"]
    assert "<redacted:url>" in args["cmd"]
    assert "/Users/<user>/projects/<project>/app.py" in args["cmd"]
    assert args["env"]["OPENAI_API_KEY"] == "<redacted:secret>"
    assert args["justification"].startswith("<redacted:")

    output = out.read_text()
    assert "brianliou" not in output
    assert "sk-secret" not in output
    assert "example.com" not in output
    assert "private API" not in output


def test_redact_claude_message_content_preserves_token_usage():
    record = {
        "sessionId": "session-1",
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-4-5-20250929",
            "content": [
                {"type": "text", "text": "Private assistant prose"},
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {
                        "file_path": "/Users/brianliou/projects/aide/src/app.py",
                        "old_string": "secret old code",
                        "new_string": "secret new code",
                    },
                },
            ],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        },
    }

    redacted, count = redact_value(record, provider="claude")

    assert count > 0
    assert redacted["sessionId"] == "session-1"
    assert redacted["message"]["model"] == "claude-sonnet-4-5-20250929"
    assert redacted["message"]["usage"] == {"input_tokens": 10, "output_tokens": 20}

    content = redacted["message"]["content"]
    assert content[0]["text"] == "<redacted:text len=23>"
    tool_input = content[1]["input"]
    assert tool_input["file_path"] == "/Users/<user>/projects/<project>/src/app.py"
    assert tool_input["old_string"] == "<redacted:old_string len=15>"
    assert tool_input["new_string"] == "<redacted:new_string len=15>"


def test_redact_invalid_jsonl_line_does_not_copy_raw_text(tmp_path):
    source = tmp_path / "bad.jsonl"
    out = tmp_path / "redacted.jsonl"
    source.write_text('{"type": "ok"}\nnot json with secret sk-secret\n')

    result = redact_jsonl_file(source, out, provider="codex")

    assert result.lines_processed == 2
    assert result.invalid_lines == 1
    assert "sk-secret" not in out.read_text()
    assert "<redacted:invalid_json>" in out.read_text()


def test_redact_codex_instruction_and_output_fields():
    record = {
        "payload": {
            "developer_instructions": "private developer instructions",
            "user_instructions": "private user instructions",
            "aggregated_output": "private command output",
            "encrypted_content": "encrypted-private-content",
            "action": {
                "query": "private search query",
                "queries": ["private query one", "private query two"],
            },
        }
    }

    redacted, count = redact_value(record, provider="codex")

    assert count == 7
    payload = redacted["payload"]
    assert payload["developer_instructions"].startswith("<redacted:")
    assert payload["user_instructions"].startswith("<redacted:")
    assert payload["aggregated_output"].startswith("<redacted:")
    assert payload["encrypted_content"].startswith("<redacted:")
    assert payload["action"]["query"].startswith("<redacted:")
    assert payload["action"]["queries"][0].startswith("<redacted:")


def test_redact_claude_camel_case_tool_result_content():
    record = {
        "type": "user",
        "lastPrompt": "private follow-up prompt",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_use",
                    "name": "WebFetch",
                    "input": {
                        "prompt": "quote the private launch plan",
                        "url": "https://example.com/private",
                    },
                }
            ],
        },
        "attachment": {
            "snippet": "def private_business_logic(): pass",
            "prompt": "private attachment prompt",
            "planContent": "private implementation plan",
        },
        "toolUseResult": {
            "filePath": "/Users/brianliou/projects/aide/src/app.py",
            "persistedOutputPath": "/private/var/folders/mt/private-output.txt",
            "originalFile": "full private source file",
            "oldString": "old private code",
            "newString": "new private code",
            "structuredPatch": [{"lines": ["+ private patch line"]}],
            "result": "private web fetch result",
            "results": ["private string result"],
            "fullOutput": "private terminal output",
            "questions": [{"question": "private approval question"}],
        },
    }

    redacted, count = redact_value(record, provider="claude")

    assert count > 0
    assert redacted["lastPrompt"].startswith("<redacted:")
    assert redacted["message"]["content"][0]["input"]["prompt"].startswith("<redacted:")
    assert redacted["message"]["content"][0]["input"]["url"] == "<redacted:url>"
    assert redacted["attachment"]["snippet"].startswith("<redacted:")
    assert redacted["attachment"]["prompt"].startswith("<redacted:")
    assert redacted["attachment"]["planContent"].startswith("<redacted:")
    assert redacted["toolUseResult"]["filePath"] == (
        "/Users/<user>/projects/<project>/src/app.py"
    )
    assert redacted["toolUseResult"]["persistedOutputPath"] == "/var/folders/<path>"
    assert redacted["toolUseResult"]["originalFile"].startswith("<redacted:")
    assert redacted["toolUseResult"]["oldString"].startswith("<redacted:")
    assert redacted["toolUseResult"]["newString"].startswith("<redacted:")
    assert redacted["toolUseResult"]["structuredPatch"][0]["lines"][0].startswith(
        "<redacted:"
    )
    assert redacted["toolUseResult"]["result"].startswith("<redacted:")
    assert redacted["toolUseResult"]["results"][0].startswith("<redacted:")
    assert redacted["toolUseResult"]["fullOutput"].startswith("<redacted:")
    assert redacted["toolUseResult"]["questions"][0]["question"].startswith(
        "<redacted:"
    )


def test_redact_codex_patch_paths_in_keys_and_diff_values():
    record = {
        "payload": {
            "type": "patch_apply_end",
            "changes": {
                "/Users/brianliou/projects/aide/src/aide/cli.py": {
                    "unified_diff": "@@ private implementation diff",
                }
            },
            "last_agent_message": "private final answer",
            "input": "private custom tool input",
            "instructions": "private tool instructions",
            "images": ["data:image/png;base64,PRIVATE"],
            "local_images": ["/Users/brianliou/Desktop/private.png"],
            "content": [{"image_url": "data:image/png;base64,PRIVATE"}],
        }
    }

    redacted, count = redact_value(record, provider="codex")

    assert count > 0
    changes = redacted["payload"]["changes"]
    assert "/Users/<user>/projects/<project>/src/aide/cli.py" in changes
    changed_file = changes["/Users/<user>/projects/<project>/src/aide/cli.py"]
    assert changed_file["unified_diff"].startswith("<redacted:")
    assert redacted["payload"]["last_agent_message"].startswith("<redacted:")
    assert redacted["payload"]["input"].startswith("<redacted:")
    assert redacted["payload"]["instructions"].startswith("<redacted:")
    assert redacted["payload"]["images"][0].startswith("<redacted:")
    assert redacted["payload"]["local_images"][0].startswith("<redacted:")
    assert redacted["payload"]["content"][0]["image_url"].startswith("<redacted:")


def test_redact_secret_fragments_without_redacting_token_vocabulary():
    command = (
        "modal token info --token abc123 "
        "OPENAI_API_KEY=sk-secret "
        "github_pat_1234567890abcdef1234567890abcdef "
        "AKIA1234567890ABCDEF "
        "xoxb-1234567890-secret"
    )

    redacted, count = redact_value({"cmd": command}, provider="codex")

    assert count == 1
    assert "modal token info" in redacted["cmd"]
    assert "--token <redacted:secret>" in redacted["cmd"]
    assert "OPENAI_API_KEY=<redacted:secret>" in redacted["cmd"]
    assert "github_pat_" not in redacted["cmd"]
    assert "AKIA1234567890ABCDEF" not in redacted["cmd"]
    assert "xoxb-" not in redacted["cmd"]


def test_redact_generic_authorization_and_cookie_headers():
    command = (
        "curl -H 'Authorization: Bearer arbitrary-token-value' "
        "-H 'Cookie: session=private; theme=dark'"
    )

    redacted, count = redact_value({"cmd": command}, provider="codex")

    assert count == 1
    assert "arbitrary-token-value" not in redacted["cmd"]
    assert "session=private" not in redacted["cmd"]
    assert "Authorization: <redacted:secret>" in redacted["cmd"]
    assert "Cookie: <redacted:secret>" in redacted["cmd"]


def test_redact_escaped_local_paths_inside_command_strings():
    command = '.venv/bin/python -c "needle = \\"/Users/brianliou/projects/aide\\""'

    redacted, count = redact_value({"cmd": command}, provider="codex")

    assert count == 1
    assert "/Users/brianliou" not in redacted["cmd"]
    assert "/Users/<user>/projects/<project>" in redacted["cmd"]


def test_redact_path_fields_can_contain_urls():
    record = {"cause": {"path": "https://example.com/private?token=abc"}}

    redacted, count = redact_value(record, provider="codex")

    assert count == 1
    assert redacted["cause"]["path"] == "<redacted:url>"
