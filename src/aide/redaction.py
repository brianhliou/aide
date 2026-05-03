"""Local JSONL redaction for provider logs.

The redactor preserves parser-relevant structure while removing sensitive prompt
text, tool output, file contents, local paths, URLs, and secrets.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_PROVIDERS = frozenset({"claude", "codex"})

_TEXT_KEYS = frozenset(
    {
        "aggregated_output",
        "args",
        "base64",
        "content",
        "customTitle",
        "data",
        "description",
        "detail",
        "developer_instructions",
        "diff",
        "encrypted_content",
        "errorDetails",
        "formatted_output",
        "fullOutput",
        "ghRateLimitHint",
        "image_url",
        "images",
        "input",
        "input_text",
        "instructions",
        "justification",
        "last_agent_message",
        "lastPrompt",
        "lines",
        "local_images",
        "message",
        "new_string",
        "newString",
        "old_string",
        "oldString",
        "originalFile",
        "output",
        "output_text",
        "pattern",
        "plan",
        "planContent",
        "preview",
        "prompt",
        "question",
        "query",
        "queries",
        "reason",
        "result",
        "results",
        "script",
        "signature",
        "snippet",
        "staleReadFileStateHint",
        "stderr",
        "stdout",
        "subject",
        "text",
        "thinking",
        "title",
        "toolUseResult",
        "unified_diff",
        "user_instructions",
        "what_you_should_do",
    }
)
_PATH_KEYS = frozenset(
    {
        "cwd",
        "displayPath",
        "file_path",
        "filePath",
        "filenames",
        "outputDir",
        "outputFile",
        "path",
        "persistedOutputPath",
        "source_file",
    }
)
_COMMAND_KEYS = frozenset({"cmd", "command"})
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|authorization|bearer|cookie|secret|password|passwd|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|\btoken\b)",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|cookie|secret|password|passwd|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|token)"
    r"(\s*[:=]\s*)[^\s\"']+"
)
_BEARER_RE = re.compile(r"(?i)bearer\s+[^\s\"']+")
_AUTH_HEADER_RE = re.compile(
    r"(?i)\b(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s\"']+"
)
_COOKIE_HEADER_RE = re.compile(r"(?i)\b(cookie\s*[:=]\s*)[^\n\"']+")
_CLI_SECRET_ARG_RE = re.compile(
    r"(?i)(--(?:api[-_]?key|token|secret|password|passwd|access[-_]?token|"
    r"refresh[-_]?token|id[-_]?token)(?:=|\s+))[^\s\"']+"
)
_KEY_VALUE_RE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{6,}\b")
_GITHUB_TOKEN_RE = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b")
_GITHUB_PAT_RE = re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")
_AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_URL_RE = re.compile(r"https?://[^\s\"')]+")
_HOME_PATH_RE = re.compile(r"/Users/([^/\s\"'\\]+)")
_PROJECT_PATH_RE = re.compile(r"(/Users/<user>/projects/)([^/\s\"'\\]+)")
_TMP_PATH_RE = re.compile(r"/(?:private/)?tmp/[^\s\"'\\]+")
_VAR_FOLDERS_PATH_RE = re.compile(r"/(?:private/)?var/folders/[^\s\"'\\]+")
_RAW_HOME_PATH_RE = re.compile(r"/Users/(?!<user>)[^/\s\"'\\]+")
_RAW_TMP_PATH_RE = re.compile(r"/(?:private/)?tmp/(?!<path>)[^\s\"'\\]+")
_RAW_VAR_FOLDERS_PATH_RE = re.compile(
    r"/(?:private/)?var/folders/(?!<path>)[^\s\"'\\]+"
)
_DATA_IMAGE_RE = re.compile(r"data:image/[^;,]+;base64,", re.IGNORECASE)
_REDACTION_PLACEHOLDER_RE = re.compile(r"^<redacted:[a-zA-Z_]+(?: len=\d+)?>$")
_SAFE_AUDIT_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_LONG_STRING_EXEMPT_KEYS = frozenset(
    {
        "arguments",
        "call_id",
        "cmd",
        "command",
        "cwd",
        "id",
        "model",
        "name",
        "parentUuid",
        "path",
        "process_id",
        "requestId",
        "sessionId",
        "source_file",
        "timestamp",
        "tool_use_id",
        "turn_id",
        "type",
        "uuid",
    }
)


@dataclass
class RedactionResult:
    """Counts from a redaction run."""

    files_processed: int = 0
    lines_processed: int = 0
    fields_redacted: int = 0
    invalid_lines: int = 0


@dataclass
class RedactionAuditResult:
    """Counts from a redacted-output audit."""

    files_scanned: int = 0
    lines_scanned: int = 0
    invalid_lines: int = 0
    findings: Counter[tuple[str, str]] | None = None

    def __post_init__(self) -> None:
        if self.findings is None:
            self.findings = Counter()

    @property
    def finding_count(self) -> int:
        return sum(self.findings.values()) if self.findings else 0

    def add_finding(self, kind: str, path: str) -> None:
        if self.findings is None:
            self.findings = Counter()
        self.findings[(kind, path)] += 1


def redact_jsonl_file(
    input_path: Path,
    output_path: Path,
    provider: str,
) -> RedactionResult:
    """Redact a single JSONL file."""
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")

    input_path = Path(input_path)
    output_path = Path(output_path)
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Refusing to overwrite source file")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = RedactionResult(files_processed=1)

    with open(input_path) as src, open(output_path, "w") as dest:
        for line in src:
            stripped = line.strip()
            if not stripped:
                continue
            result.lines_processed += 1
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                result.invalid_lines += 1
                dest.write(json.dumps({"type": "invalid", "raw": "<redacted:invalid_json>"}) + "\n")
                result.fields_redacted += 1
                continue

            redacted, count = redact_value(data, provider=provider)
            result.fields_redacted += count
            dest.write(json.dumps(redacted, sort_keys=True, separators=(",", ":")) + "\n")

    return result


def redact_path(
    input_path: Path,
    output_path: Path,
    provider: str,
) -> RedactionResult:
    """Redact one JSONL file or a directory tree of JSONL files."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    if input_path.is_dir():
        total = RedactionResult()
        for source in sorted(input_path.rglob("*.jsonl")):
            dest = output_path / source.relative_to(input_path)
            item = redact_jsonl_file(source, dest, provider)
            total.files_processed += item.files_processed
            total.lines_processed += item.lines_processed
            total.fields_redacted += item.fields_redacted
            total.invalid_lines += item.invalid_lines
        return total
    return redact_jsonl_file(input_path, output_path, provider)


def audit_redacted_path(
    input_path: Path,
    max_string_length: int = 500,
) -> RedactionAuditResult:
    """Scan redacted JSONL output for likely sensitive leftovers."""
    input_path = Path(input_path)
    result = RedactionAuditResult()
    files = sorted(input_path.rglob("*.jsonl")) if input_path.is_dir() else [input_path]

    for file_path in files:
        result.files_scanned += 1
        with open(file_path) as src:
            for line in src:
                stripped = line.strip()
                if not stripped:
                    continue
                result.lines_scanned += 1
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    result.invalid_lines += 1
                    result.add_finding("invalid_json", "<line>")
                    continue
                _audit_value(data, result, max_string_length=max_string_length)

    return result


def redact_value(value: Any, provider: str, key: str | None = None) -> tuple[Any, int]:
    """Redact a JSON-compatible value, returning (value, redaction_count)."""
    if isinstance(value, dict):
        return _redact_dict(value, provider)
    if isinstance(value, list):
        redacted_items = []
        count = 0
        for item in value:
            redacted, item_count = redact_value(item, provider=provider, key=key)
            redacted_items.append(redacted)
            count += item_count
        return redacted_items, count
    if isinstance(value, str):
        return _redact_string(value, key=key)
    return value, 0


def _redact_dict(data: dict[str, Any], provider: str) -> tuple[dict[str, Any], int]:
    redacted: dict[str, Any] = {}
    count = 0
    for key, value in data.items():
        output_key = _redact_key(key)
        if output_key != key:
            count += 1

        if _SECRET_KEY_RE.search(key):
            redacted[output_key] = "<redacted:secret>"
            count += 1
            continue

        if key == "arguments" and isinstance(value, str):
            parsed = _loads_object(value)
            if parsed is not None:
                redacted_args, item_count = redact_value(parsed, provider=provider)
                redacted[output_key] = json.dumps(
                    redacted_args, sort_keys=True, separators=(",", ":")
                )
                count += item_count
                continue

        redacted_value, item_count = redact_value(value, provider=provider, key=key)
        redacted[output_key] = redacted_value
        count += item_count
    return redacted, count


def _redact_string(value: str, key: str | None) -> tuple[str, int]:
    if key in _PATH_KEYS:
        redacted = _redact_sensitive_fragments(value)
        return redacted, int(redacted != value)

    if key in _COMMAND_KEYS:
        redacted = _redact_command(value)
        return redacted, int(redacted != value)

    if key in _TEXT_KEYS:
        return f"<redacted:{key} len={len(value)}>", 1

    redacted = _redact_sensitive_fragments(value)
    return redacted, int(redacted != value)


def _redact_command(command: str) -> str:
    redacted = _redact_path_string(command)
    redacted = _redact_sensitive_fragments(redacted)
    return redacted


def _redact_sensitive_fragments(value: str) -> str:
    value = _PRIVATE_KEY_RE.sub("<redacted:secret>", value)
    value = _AUTH_HEADER_RE.sub(lambda m: f"{m.group(1)}<redacted:secret>", value)
    value = _COOKIE_HEADER_RE.sub(lambda m: f"{m.group(1)}<redacted:secret>", value)
    value = _SECRET_ASSIGNMENT_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}<redacted:secret>", value
    )
    value = _BEARER_RE.sub("Bearer <redacted:secret>", value)
    value = _CLI_SECRET_ARG_RE.sub(lambda m: f"{m.group(1)}<redacted:secret>", value)
    value = _KEY_VALUE_RE.sub("<redacted:secret>", value)
    value = _GITHUB_PAT_RE.sub("<redacted:secret>", value)
    value = _GITHUB_TOKEN_RE.sub("<redacted:secret>", value)
    value = _AWS_ACCESS_KEY_RE.sub("<redacted:secret>", value)
    value = _SLACK_TOKEN_RE.sub("<redacted:secret>", value)
    value = _URL_RE.sub("<redacted:url>", value)
    value = _redact_path_string(value)
    return value


def _redact_key(key: str) -> str:
    path_markers = (
        "/Users/",
        "/tmp/",
        "/private/tmp/",
        "/var/folders/",
        "/private/var/folders/",
    )
    if not any(marker in key for marker in path_markers):
        return key
    return _redact_path_string(key)


def _redact_path_string(value: str) -> str:
    redacted = _HOME_PATH_RE.sub("/Users/<user>", value)
    redacted = _PROJECT_PATH_RE.sub(r"\1<project>", redacted)
    redacted = _TMP_PATH_RE.sub("/tmp/<path>", redacted)
    redacted = _VAR_FOLDERS_PATH_RE.sub("/var/folders/<path>", redacted)
    return redacted


def _loads_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _audit_value(
    value: Any,
    result: RedactionAuditResult,
    path: str = "<root>",
    key: str | None = None,
    max_string_length: int = 500,
) -> None:
    if isinstance(value, dict):
        for item_key, item_value in value.items():
            segment = _audit_path_segment(item_key)
            item_path = f"{path}.{segment}" if path != "<root>" else segment
            _audit_key(item_key, result, item_path)
            _audit_value(
                item_value,
                result,
                path=item_path,
                key=item_key,
                max_string_length=max_string_length,
            )
        return

    if isinstance(value, list):
        item_path = f"{path}[]"
        for item in value:
            _audit_value(
                item,
                result,
                path=item_path,
                key=key,
                max_string_length=max_string_length,
            )
        return

    if isinstance(value, str):
        _audit_string(value, result, path, key, max_string_length)


def _audit_key(key: str, result: RedactionAuditResult, path: str) -> None:
    if _RAW_HOME_PATH_RE.search(key):
        result.add_finding("raw_home_path_key", path)
    if _RAW_TMP_PATH_RE.search(key) or _RAW_VAR_FOLDERS_PATH_RE.search(key):
        result.add_finding("raw_temp_path_key", path)
    if _contains_secret(key):
        result.add_finding("secret_key", path)


def _audit_string(
    value: str,
    result: RedactionAuditResult,
    path: str,
    key: str | None,
    max_string_length: int,
) -> None:
    if _REDACTION_PLACEHOLDER_RE.match(value):
        return

    if _RAW_HOME_PATH_RE.search(value):
        result.add_finding("raw_home_path", path)
    if _RAW_TMP_PATH_RE.search(value) or _RAW_VAR_FOLDERS_PATH_RE.search(value):
        result.add_finding("raw_temp_path", path)
    if _URL_RE.search(value):
        result.add_finding("raw_url", path)
    if _DATA_IMAGE_RE.search(value):
        result.add_finding("image_payload", path)
    if _contains_secret(value, key=key):
        result.add_finding("secret", path)
    if len(value) > max_string_length and key not in _LONG_STRING_EXEMPT_KEYS:
        result.add_finding("long_text", path)


def _audit_path_segment(key: str) -> str:
    if _SAFE_AUDIT_KEY_RE.match(key):
        return key
    return "<key>"


def _contains_secret(value: str, key: str | None = None) -> bool:
    candidate = value.replace("<redacted:secret>", "")
    if key in _COMMAND_KEYS or key == "arguments":
        return any(
            pattern.search(candidate)
            for pattern in (
                _PRIVATE_KEY_RE,
                _CLI_SECRET_ARG_RE,
                _KEY_VALUE_RE,
                _GITHUB_PAT_RE,
                _GITHUB_TOKEN_RE,
                _AWS_ACCESS_KEY_RE,
                _SLACK_TOKEN_RE,
            )
        )

    return any(
        pattern.search(candidate)
        for pattern in (
            _PRIVATE_KEY_RE,
            _AUTH_HEADER_RE,
            _COOKIE_HEADER_RE,
            _SECRET_ASSIGNMENT_RE,
            _BEARER_RE,
            _CLI_SECRET_ARG_RE,
            _KEY_VALUE_RE,
            _GITHUB_PAT_RE,
            _GITHUB_TOKEN_RE,
            _AWS_ACCESS_KEY_RE,
            _SLACK_TOKEN_RE,
        )
    )
