"""Tests for the JSONL parser module."""

from __future__ import annotations

import json

from aide.parser import (
    _derive_project_name,
    discover_jsonl_files,
    parse_jsonl_file,
)

# ---------------------------------------------------------------------------
# parse_jsonl_file — sample.jsonl (session-001)
# ---------------------------------------------------------------------------


class TestParseSample1:
    def test_returns_one_session(self, sample_jsonl):
        sessions = parse_jsonl_file(sample_jsonl)
        assert len(sessions) == 1

    def test_session_id(self, sample_jsonl):
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.session_id == "session-001"

    def test_file_history_snapshot_skipped(self, sample_jsonl):
        """The first line is file-history-snapshot and must be excluded."""
        session = parse_jsonl_file(sample_jsonl)[0]
        for msg in session.messages:
            assert msg.type != "file-history-snapshot"

    def test_message_count(self, sample_jsonl):
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.message_count == 8
        assert session.user_message_count == 4
        assert session.assistant_message_count == 4

    def test_token_totals(self, sample_jsonl):
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.total_input_tokens == 9000
        assert session.total_output_tokens == 800
        assert session.total_cache_creation_tokens == 3000
        assert session.total_cache_read_tokens == 15500

    def test_tool_call_counts(self, sample_jsonl):
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.tool_call_count == 3
        assert session.file_read_count == 1
        assert session.file_edit_count == 1
        assert session.bash_count == 1
        assert session.file_write_count == 0

    def test_cost_estimate(self, sample_jsonl):
        session = parse_jsonl_file(sample_jsonl)[0]
        # Cost module rounds to 4 decimal places
        assert session.estimated_cost_usd == 0.0549

    def test_timestamps_and_duration(self, sample_jsonl):
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.started_at.minute == 0
        assert session.started_at.second == 1
        assert session.ended_at.second == 35
        assert session.duration_seconds == 34

    def test_project_derivation_from_fixtures_dir(self, sample_jsonl):
        """Fixtures live in tests/fixtures/ — project_path is 'fixtures'."""
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.project_path == "fixtures"
        # No '-projects-' marker, so fallback returns last segment
        assert session.project_name == "fixtures"

    def test_source_file(self, sample_jsonl):
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.source_file == str(sample_jsonl)

    def test_tool_calls_have_file_paths(self, sample_jsonl):
        session = parse_jsonl_file(sample_jsonl)[0]
        all_tcs = [tc for m in session.messages for tc in m.tool_calls]
        read_tc = [tc for tc in all_tcs if tc.tool_name == "Read"][0]
        assert read_tc.file_path == "/Users/testuser/projects/my-project/src/parser.py"

    def test_content_length(self, sample_jsonl):
        """User msg-001 has string content; assistant msgs have text items."""
        session = parse_jsonl_file(sample_jsonl)[0]
        msg_001 = [m for m in session.messages if m.uuid == "msg-001"][0]
        assert msg_001.content_length == len("Fix the bug in parser.py")


# ---------------------------------------------------------------------------
# parse_jsonl_file — sample2.jsonl (session-002)
# ---------------------------------------------------------------------------


class TestParseSample2:
    def test_session_id(self, sample2_jsonl):
        session = parse_jsonl_file(sample2_jsonl)[0]
        assert session.session_id == "session-002"

    def test_message_count(self, sample2_jsonl):
        session = parse_jsonl_file(sample2_jsonl)[0]
        assert session.message_count == 6
        assert session.user_message_count == 3
        assert session.assistant_message_count == 3

    def test_token_totals(self, sample2_jsonl):
        session = parse_jsonl_file(sample2_jsonl)[0]
        assert session.total_input_tokens == 10000
        assert session.total_output_tokens == 730
        assert session.total_cache_creation_tokens == 2000
        assert session.total_cache_read_tokens == 6000

    def test_tool_call_counts(self, sample2_jsonl):
        session = parse_jsonl_file(sample2_jsonl)[0]
        assert session.tool_call_count == 3
        assert session.file_read_count == 2
        assert session.file_write_count == 1
        assert session.file_edit_count == 0
        assert session.bash_count == 0

    def test_cost_estimate(self, sample2_jsonl):
        session = parse_jsonl_file(sample2_jsonl)[0]
        # Cost module rounds to 4 decimal places
        assert session.estimated_cost_usd == 0.0503

    def test_duration(self, sample2_jsonl):
        session = parse_jsonl_file(sample2_jsonl)[0]
        assert session.duration_seconds == 44


# ---------------------------------------------------------------------------
# compact_boundary system messages
# ---------------------------------------------------------------------------


class TestCompactBoundary:
    def test_compact_boundary_handled(self, tmp_path):
        """Lines with no message field (e.g. compact_boundary) are system messages with 0 tokens."""
        jsonl = tmp_path / "compact.jsonl"
        import json

        lines = [
            json.dumps(
                {
                    "type": "system",
                    "subtype": "compact_boundary",
                    "sessionId": "sess-cb",
                    "uuid": "cb-001",
                    "parentUuid": None,
                    "timestamp": "2026-01-01T00:00:00.000Z",
                    "content": "Context was compacted.",
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "sess-cb",
                    "uuid": "cb-002",
                    "parentUuid": "cb-001",
                    "timestamp": "2026-01-01T00:00:05.000Z",
                    "message": {"role": "user", "content": "Hello"},
                }
            ),
        ]
        jsonl.write_text("\n".join(lines) + "\n")

        sessions = parse_jsonl_file(jsonl)
        assert len(sessions) == 1
        session = sessions[0]
        assert session.message_count == 2

        system_msg = [m for m in session.messages if m.role == "system"][0]
        assert system_msg.input_tokens == 0
        assert system_msg.output_tokens == 0
        assert system_msg.cache_read_tokens == 0
        assert system_msg.cache_creation_tokens == 0
        assert system_msg.content_length == len("Context was compacted.")


# ---------------------------------------------------------------------------
# discover_jsonl_files
# ---------------------------------------------------------------------------


class TestDiscoverJsonlFiles:
    def test_finds_jsonl_files(self, tmp_path):
        (tmp_path / "a.jsonl").touch()
        (tmp_path / "b.jsonl").touch()
        (tmp_path / "c.txt").touch()
        found = discover_jsonl_files(tmp_path)
        assert len(found) == 2
        assert all(f.suffix == ".jsonl" for f in found)

    def test_excludes_subagents(self, tmp_path):
        (tmp_path / "main.jsonl").touch()
        subdir = tmp_path / "subagents"
        subdir.mkdir()
        (subdir / "agent.jsonl").touch()
        found = discover_jsonl_files(tmp_path)
        assert len(found) == 1
        assert found[0].name == "main.jsonl"

    def test_recurses_into_subdirectories(self, tmp_path):
        proj = tmp_path / "project1"
        proj.mkdir()
        (proj / "session.jsonl").touch()
        found = discover_jsonl_files(tmp_path)
        assert len(found) == 1

    def test_empty_directory(self, tmp_path):
        found = discover_jsonl_files(tmp_path)
        assert found == []


# ---------------------------------------------------------------------------
# project name derivation
# ---------------------------------------------------------------------------


class TestProjectNameDerivation:
    def test_standard_path(self):
        assert _derive_project_name("-Users-brianliou-projects-slopfarm") == "slopfarm"

    def test_nested_projects_marker(self):
        """Use the last -projects- occurrence."""
        name = _derive_project_name("-Users-bob-projects-foo-projects-bar")
        assert name == "bar"

    def test_no_projects_marker(self):
        """Fallback: last segment after last dash."""
        assert _derive_project_name("some-random-name") == "name"

    def test_single_word(self):
        assert _derive_project_name("standalone") == "standalone"

    def test_deep_path(self):
        name = _derive_project_name("-Users-alice-projects-deep-nested-thing")
        assert name == "deep-nested-thing"


# ---------------------------------------------------------------------------
# compaction detection
# ---------------------------------------------------------------------------


class TestCompactionDetection:
    """Tests for compaction_count and peak_context_tokens computation."""

    def test_no_compaction_in_sample(self, sample_jsonl):
        """Sample sessions have small context — no compactions."""
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.compaction_count == 0

    def test_peak_context_tokens_in_sample(self, sample_jsonl):
        """Peak context is max(input + cache_read + cache_creation) across assistant messages."""
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.peak_context_tokens > 0

    def test_compaction_detected(self, tmp_path):
        """A >50% context drop from >100K triggers compaction detection."""
        import json

        lines = [
            # Assistant msg 1: context = 200K (above 100K threshold)
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-compact",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "content": "hello",
                    "usage": {
                        "input_tokens": 150000,
                        "output_tokens": 100,
                        "cache_read_input_tokens": 50000,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }),
            # Assistant msg 2: context drops to 40K (<50% of 200K)
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-compact",
                "uuid": "m2",
                "parentUuid": "m1",
                "timestamp": "2026-01-01T00:00:02.000Z",
                "message": {
                    "role": "assistant",
                    "content": "world",
                    "usage": {
                        "input_tokens": 30000,
                        "output_tokens": 200,
                        "cache_read_input_tokens": 10000,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }),
            # Assistant msg 3: context grows to 180K
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-compact",
                "uuid": "m3",
                "parentUuid": "m2",
                "timestamp": "2026-01-01T00:00:03.000Z",
                "message": {
                    "role": "assistant",
                    "content": "again",
                    "usage": {
                        "input_tokens": 120000,
                        "output_tokens": 300,
                        "cache_read_input_tokens": 60000,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }),
            # Assistant msg 4: drops to 50K (<50% of 180K)
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-compact",
                "uuid": "m4",
                "parentUuid": "m3",
                "timestamp": "2026-01-01T00:00:04.000Z",
                "message": {
                    "role": "assistant",
                    "content": "final",
                    "usage": {
                        "input_tokens": 40000,
                        "output_tokens": 50,
                        "cache_read_input_tokens": 10000,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }),
        ]
        jsonl = tmp_path / "compact-test.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert session.compaction_count == 2
        assert session.peak_context_tokens == 200000

    def test_no_compaction_below_threshold(self, tmp_path):
        """Context drop below 100K threshold is not counted as compaction."""
        lines = [
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-small",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "content": "hi",
                    "usage": {
                        "input_tokens": 80000,
                        "output_tokens": 100,
                        "cache_read_input_tokens": 10000,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }),
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-small",
                "uuid": "m2",
                "parentUuid": "m1",
                "timestamp": "2026-01-01T00:00:02.000Z",
                "message": {
                    "role": "assistant",
                    "content": "lo",
                    "usage": {
                        "input_tokens": 10000,
                        "output_tokens": 50,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }),
        ]
        jsonl = tmp_path / "no-compact.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert session.compaction_count == 0
        assert session.peak_context_tokens == 90000


# ---------------------------------------------------------------------------
# New field extractions from sample.jsonl
# ---------------------------------------------------------------------------


class TestNewFieldsSample1:
    """Test new field extractions from sample.jsonl which has real-ish data."""

    def test_model_extracted(self, sample_jsonl):
        """Assistant messages should have model extracted."""
        session = parse_jsonl_file(sample_jsonl)[0]
        asst_msgs = [m for m in session.messages if m.role == "assistant"]
        for m in asst_msgs:
            assert m.model == "claude-sonnet-4-5-20250929"

    def test_stop_reason_extracted(self, sample_jsonl):
        """Assistant messages should have stop_reason."""
        session = parse_jsonl_file(sample_jsonl)[0]
        asst_msgs = [m for m in session.messages if m.role == "assistant"]
        # First 3 assistant messages stop for tool_use, last one for end_turn
        assert asst_msgs[-1].stop_reason == "end_turn"
        for m in asst_msgs[:-1]:
            assert m.stop_reason == "tool_use"

    def test_user_messages_have_no_model(self, sample_jsonl):
        session = parse_jsonl_file(sample_jsonl)[0]
        user_msgs = [m for m in session.messages if m.role == "user"]
        for m in user_msgs:
            assert m.model is None

    def test_prompt_length_string_content(self, sample_jsonl):
        """User msg-001 has string content 'Fix the bug in parser.py'."""
        session = parse_jsonl_file(sample_jsonl)[0]
        msg = [m for m in session.messages if m.uuid == "msg-001"][0]
        assert msg.prompt_length == len("Fix the bug in parser.py")

    def test_prompt_length_tool_result(self, sample_jsonl):
        """User messages with tool_result content have no user text."""
        session = parse_jsonl_file(sample_jsonl)[0]
        # msg-003 is a tool_result user message
        msg = [m for m in session.messages if m.uuid == "msg-003"][0]
        assert msg.prompt_length == 0

    def test_tool_use_ids_extracted(self, sample_jsonl):
        """Tool calls should have tool_use_id from the JSONL."""
        session = parse_jsonl_file(sample_jsonl)[0]
        all_tcs = [tc for m in session.messages for tc in m.tool_calls]
        read_tc = [tc for tc in all_tcs if tc.tool_name == "Read"][0]
        assert read_tc.tool_use_id == "toolu_01test001"
        edit_tc = [tc for tc in all_tcs if tc.tool_name == "Edit"][0]
        assert edit_tc.tool_use_id == "toolu_01test002"

    def test_bash_command_extracted(self, sample_jsonl):
        """Bash tool calls should have command field."""
        session = parse_jsonl_file(sample_jsonl)[0]
        all_tcs = [tc for m in session.messages for tc in m.tool_calls]
        bash_tc = [tc for tc in all_tcs if tc.tool_name == "Bash"][0]
        assert bash_tc.command == "pytest tests/ -v"

    def test_edit_sizes_extracted(self, sample_jsonl):
        """Edit tool calls should have old_string_len and new_string_len."""
        session = parse_jsonl_file(sample_jsonl)[0]
        all_tcs = [tc for m in session.messages for tc in m.tool_calls]
        edit_tc = [tc for tc in all_tcs if tc.tool_name == "Edit"][0]
        assert edit_tc.old_string_len == len("broken code")
        assert edit_tc.new_string_len == len("fixed code")

    def test_git_branch_extracted(self, sample_jsonl):
        """Git branch should be extracted from first user message."""
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.git_branch == "main"

    def test_no_errors_in_sample(self, sample_jsonl):
        """Sample has no tool errors."""
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.tool_error_count == 0

    def test_test_after_edit_rate(self, sample_jsonl):
        """In sample.jsonl: Edit in msg-004 is followed by Bash in msg-006.
        But they're in different assistant messages, so rate = 0."""
        session = parse_jsonl_file(sample_jsonl)[0]
        # Edit and Bash are in separate assistant messages
        assert session.test_after_edit_rate == 0.0


# ---------------------------------------------------------------------------
# is_error correlation
# ---------------------------------------------------------------------------


class TestIsErrorCorrelation:
    def test_error_marked_on_tool_call(self, tmp_path):
        """Tool calls with error results should have is_error=True."""
        lines = [
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-err",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "toolu_good", "name": "Read",
                         "input": {"file_path": "/good.py"}},
                        {"type": "tool_use", "id": "toolu_bad", "name": "Bash",
                         "input": {"command": "bad-cmd"}},
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            }),
            json.dumps({
                "type": "user",
                "sessionId": "sess-err",
                "uuid": "m2",
                "parentUuid": "m1",
                "timestamp": "2026-01-01T00:00:02.000Z",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_good",
                         "content": "file contents"},
                        {"type": "tool_result", "tool_use_id": "toolu_bad",
                         "is_error": True, "content": "command not found"},
                    ],
                },
            }),
        ]
        jsonl = tmp_path / "error.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        all_tcs = [tc for m in session.messages for tc in m.tool_calls]
        good_tc = [tc for tc in all_tcs if tc.tool_use_id == "toolu_good"][0]
        bad_tc = [tc for tc in all_tcs if tc.tool_use_id == "toolu_bad"][0]

        assert good_tc.is_error is False
        assert bad_tc.is_error is True
        assert session.tool_error_count == 1

    def test_no_errors(self, tmp_path):
        """No is_error flags means tool_error_count = 0."""
        lines = [
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-ok",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "toolu_ok", "name": "Read",
                         "input": {"file_path": "/ok.py"}},
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            }),
            json.dumps({
                "type": "user",
                "sessionId": "sess-ok",
                "uuid": "m2",
                "parentUuid": "m1",
                "timestamp": "2026-01-01T00:00:02.000Z",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_ok",
                         "content": "all good"},
                    ],
                },
            }),
        ]
        jsonl = tmp_path / "no-error.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert session.tool_error_count == 0


# ---------------------------------------------------------------------------
# Custom title
# ---------------------------------------------------------------------------


class TestCustomTitle:
    def test_custom_title_extracted(self, tmp_path):
        lines = [
            json.dumps({
                "type": "custom-title",
                "sessionId": "sess-title",
                "customTitle": "Fix auth bug",
            }),
            json.dumps({
                "type": "user",
                "sessionId": "sess-title",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {"role": "user", "content": "hello"},
            }),
        ]
        jsonl = tmp_path / "title.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert session.custom_title == "Fix auth bug"

    def test_no_custom_title(self, sample_jsonl):
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.custom_title is None


# ---------------------------------------------------------------------------
# Turn duration
# ---------------------------------------------------------------------------


class TestTurnDuration:
    def test_turn_duration_extracted(self, tmp_path):
        lines = [
            json.dumps({
                "type": "user",
                "sessionId": "sess-td",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {"role": "user", "content": "go"},
            }),
            json.dumps({
                "type": "system",
                "subtype": "turn_duration",
                "sessionId": "sess-td",
                "durationMs": 5000,
                "timestamp": "2026-01-01T00:00:06.000Z",
            }),
            json.dumps({
                "type": "system",
                "subtype": "turn_duration",
                "sessionId": "sess-td",
                "durationMs": 12000,
                "timestamp": "2026-01-01T00:00:18.000Z",
            }),
        ]
        jsonl = tmp_path / "turn.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert session.turn_count == 2
        assert session.total_turn_duration_ms == 17000
        assert session.max_turn_duration_ms == 12000

    def test_no_turn_duration(self, sample_jsonl):
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.turn_count == 0
        assert session.total_turn_duration_ms == 0
        assert session.max_turn_duration_ms == 0


# ---------------------------------------------------------------------------
# Compact boundary with metadata (preTokens)
# ---------------------------------------------------------------------------


class TestCompactBoundaryMetadata:
    def test_compact_boundary_with_pretokens(self, tmp_path):
        """compact_boundary with compactMetadata.preTokens replaces heuristic."""
        lines = [
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-cbm",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "content": "working",
                    "usage": {"input_tokens": 50000, "output_tokens": 100,
                              "cache_read_input_tokens": 10000, "cache_creation_input_tokens": 0},
                },
            }),
            json.dumps({
                "type": "system",
                "subtype": "compact_boundary",
                "sessionId": "sess-cbm",
                "uuid": "cb1",
                "parentUuid": "m1",
                "timestamp": "2026-01-01T00:01:00.000Z",
                "compactMetadata": {"preTokens": 180000},
                "content": "Context was compacted.",
            }),
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-cbm",
                "uuid": "m2",
                "parentUuid": "cb1",
                "timestamp": "2026-01-01T00:01:01.000Z",
                "message": {
                    "role": "assistant",
                    "content": "continuing",
                    "usage": {"input_tokens": 20000, "output_tokens": 50,
                              "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 0},
                },
            }),
        ]
        jsonl = tmp_path / "cbm.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert session.compaction_count == 1
        # peak should be max(preTokens=180K, max_context=60K) = 180K
        assert session.peak_context_tokens == 180000


# ---------------------------------------------------------------------------
# Rework file count
# ---------------------------------------------------------------------------


class TestReworkFileCount:
    def test_rework_detected(self, tmp_path):
        """Files edited 3+ times count as rework."""
        lines = [
            # Assistant with 3 edits to same file + 1 write to another
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-rw",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Edit",
                         "input": {"file_path": "/a.py", "old_string": "x", "new_string": "y"}},
                        {"type": "tool_use", "id": "t2", "name": "Edit",
                         "input": {"file_path": "/a.py", "old_string": "a", "new_string": "b"}},
                        {"type": "tool_use", "id": "t3", "name": "Edit",
                         "input": {"file_path": "/a.py", "old_string": "c", "new_string": "d"}},
                        {"type": "tool_use", "id": "t4", "name": "Write",
                         "input": {"file_path": "/b.py", "content": "new"}},
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            }),
        ]
        jsonl = tmp_path / "rework.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert session.rework_file_count == 1  # /a.py edited 3 times

    def test_no_rework(self, sample_jsonl):
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.rework_file_count == 0


# ---------------------------------------------------------------------------
# Test-after-edit rate
# ---------------------------------------------------------------------------


class TestTestAfterEditRate:
    def test_edit_followed_by_bash_counted(self, tmp_path):
        """Edit followed by Bash in same message = tested edit."""
        lines = [
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-tae",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Edit",
                         "input": {"file_path": "/a.py", "old_string": "x", "new_string": "y"}},
                        {"type": "tool_use", "id": "t2", "name": "Bash",
                         "input": {"command": "pytest"}},
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            }),
        ]
        jsonl = tmp_path / "tae.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert session.test_after_edit_rate == 1.0

    def test_edit_without_bash_not_counted(self, tmp_path):
        """Edit NOT followed by Bash in same message = not tested."""
        lines = [
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-tae2",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Edit",
                         "input": {"file_path": "/a.py", "old_string": "x", "new_string": "y"}},
                        {"type": "tool_use", "id": "t2", "name": "Read",
                         "input": {"file_path": "/b.py"}},
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            }),
        ]
        jsonl = tmp_path / "tae2.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert session.test_after_edit_rate == 0.0

    def test_mixed_rate(self, tmp_path):
        """Two edits, one followed by Bash = 0.5 rate."""
        lines = [
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-tae3",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Edit",
                         "input": {"file_path": "/a.py", "old_string": "x", "new_string": "y"}},
                        {"type": "tool_use", "id": "t2", "name": "Bash",
                         "input": {"command": "pytest"}},
                        {"type": "tool_use", "id": "t3", "name": "Edit",
                         "input": {"file_path": "/b.py", "old_string": "a", "new_string": "b"}},
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            }),
        ]
        jsonl = tmp_path / "tae3.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert session.test_after_edit_rate == 0.5


# ---------------------------------------------------------------------------
# Write tool new_string_len
# ---------------------------------------------------------------------------


class TestWriteSizeExtraction:
    def test_write_content_length(self, sample2_jsonl):
        """Write tool call should capture content length as new_string_len."""
        session = parse_jsonl_file(sample2_jsonl)[0]
        all_tcs = [tc for m in session.messages for tc in m.tool_calls]
        write_tc = [tc for tc in all_tcs if tc.tool_name == "Write"][0]
        assert write_tc.new_string_len == len("# new feature code")
        assert write_tc.old_string_len is None


# ---------------------------------------------------------------------------
# Thinking blocks
# ---------------------------------------------------------------------------


class TestThinkingBlocks:
    def test_thinking_chars_from_sample(self, sample_jsonl):
        """sample.jsonl msg-002 has a thinking block 'Let me read the file first.'"""
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.total_thinking_chars == len("Let me read the file first.")
        assert session.thinking_message_count == 1

    def test_thinking_chars_per_message(self, sample_jsonl):
        """The specific message with thinking should have thinking_chars set."""
        session = parse_jsonl_file(sample_jsonl)[0]
        msg = [m for m in session.messages if m.uuid == "msg-002"][0]
        assert msg.thinking_chars == len("Let me read the file first.")

    def test_no_thinking_message(self, sample_jsonl):
        """Messages without thinking blocks should have 0 thinking_chars."""
        session = parse_jsonl_file(sample_jsonl)[0]
        msg = [m for m in session.messages if m.uuid == "msg-004"][0]
        assert msg.thinking_chars == 0

    def test_multiple_thinking_blocks(self, tmp_path):
        """Multiple thinking blocks in one message are summed."""
        lines = [
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-think",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "First thought.", "signature": "sig1"},
                        {"type": "text", "text": "Some text."},
                        {"type": "thinking", "thinking": "Second thought here.",
                         "signature": "sig2"},
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            }),
        ]
        jsonl = tmp_path / "thinking.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert session.total_thinking_chars == len("First thought.") + len("Second thought here.")
        assert session.thinking_message_count == 1

    def test_no_thinking(self, tmp_path):
        """Sessions without thinking blocks have 0 metrics."""
        lines = [
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-nothink",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello."}],
                    "usage": {"input_tokens": 100, "output_tokens": 50,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            }),
        ]
        jsonl = tmp_path / "nothink.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert session.total_thinking_chars == 0
        assert session.thinking_message_count == 0


# ---------------------------------------------------------------------------
# Permission mode
# ---------------------------------------------------------------------------


class TestPermissionMode:
    def test_permission_mode_extracted(self, tmp_path):
        """Permission mode from user messages is captured."""
        lines = [
            json.dumps({
                "type": "user",
                "sessionId": "sess-perm",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "permissionMode": "acceptEdits",
                "message": {"role": "user", "content": "hello"},
            }),
            json.dumps({
                "type": "user",
                "sessionId": "sess-perm",
                "uuid": "m2",
                "parentUuid": "m1",
                "timestamp": "2026-01-01T00:00:05.000Z",
                "permissionMode": "acceptEdits",
                "message": {"role": "user", "content": "more"},
            }),
        ]
        jsonl = tmp_path / "perm.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert session.permission_mode == "acceptEdits"

    def test_most_common_mode(self, tmp_path):
        """When modes differ, the most common one wins."""
        lines = [
            json.dumps({
                "type": "user",
                "sessionId": "sess-perm2",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "permissionMode": "default",
                "message": {"role": "user", "content": "a"},
            }),
            json.dumps({
                "type": "user",
                "sessionId": "sess-perm2",
                "uuid": "m2",
                "parentUuid": "m1",
                "timestamp": "2026-01-01T00:00:02.000Z",
                "permissionMode": "acceptEdits",
                "message": {"role": "user", "content": "b"},
            }),
            json.dumps({
                "type": "user",
                "sessionId": "sess-perm2",
                "uuid": "m3",
                "parentUuid": "m2",
                "timestamp": "2026-01-01T00:00:03.000Z",
                "permissionMode": "acceptEdits",
                "message": {"role": "user", "content": "c"},
            }),
        ]
        jsonl = tmp_path / "perm2.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert session.permission_mode == "acceptEdits"

    def test_no_permission_mode(self, sample_jsonl):
        """sample.jsonl has no permissionMode field."""
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.permission_mode is None


# ---------------------------------------------------------------------------
# Work blocks
# ---------------------------------------------------------------------------


class TestWorkBlocks:
    def test_single_block_no_gaps(self, sample_jsonl):
        """All messages close together -> 1 work block."""
        session = parse_jsonl_file(sample_jsonl)[0]
        assert len(session.work_blocks) == 1
        assert session.work_blocks[0].block_index == 0
        assert session.work_blocks[0].message_count == session.message_count

    def test_active_duration_equals_wall_clock_for_short_session(
        self, sample_jsonl,
    ):
        """Short session with no gaps: active == wall clock."""
        session = parse_jsonl_file(sample_jsonl)[0]
        assert session.active_duration_seconds == session.duration_seconds

    def test_gap_splits_into_two_blocks(self, tmp_path):
        """A 45-min gap between messages creates 2 work blocks."""
        lines = [
            json.dumps({
                "type": "user",
                "sessionId": "sess-wb",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T10:00:00.000Z",
                "message": {"role": "user", "content": "start"},
            }),
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-wb",
                "uuid": "m2",
                "parentUuid": "m1",
                "timestamp": "2026-01-01T10:05:00.000Z",
                "message": {
                    "role": "assistant",
                    "content": "response",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }),
            # 45-minute gap here (> 30 min threshold)
            json.dumps({
                "type": "user",
                "sessionId": "sess-wb",
                "uuid": "m3",
                "parentUuid": "m2",
                "timestamp": "2026-01-01T10:50:00.000Z",
                "message": {"role": "user", "content": "back"},
            }),
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-wb",
                "uuid": "m4",
                "parentUuid": "m3",
                "timestamp": "2026-01-01T10:55:00.000Z",
                "message": {
                    "role": "assistant",
                    "content": "wb",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }),
        ]
        jsonl = tmp_path / "wb.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert len(session.work_blocks) == 2
        assert session.work_blocks[0].block_index == 0
        assert session.work_blocks[1].block_index == 1
        # Block 1: 10:00-10:05 = 5 min
        assert session.work_blocks[0].duration_seconds == 300
        assert session.work_blocks[0].message_count == 2
        # Block 2: 10:50-10:55 = 5 min
        assert session.work_blocks[1].duration_seconds == 300
        assert session.work_blocks[1].message_count == 2
        # Active duration = 5 + 5 = 10 min
        assert session.active_duration_seconds == 600
        # Wall clock = 55 min
        assert session.duration_seconds == 3300

    def test_exact_30min_does_not_split(self, tmp_path):
        """Gap of exactly 30 min should NOT split (threshold is >)."""
        lines = [
            json.dumps({
                "type": "user",
                "sessionId": "sess-exact",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T10:00:00.000Z",
                "message": {"role": "user", "content": "a"},
            }),
            json.dumps({
                "type": "user",
                "sessionId": "sess-exact",
                "uuid": "m2",
                "parentUuid": "m1",
                "timestamp": "2026-01-01T10:30:00.000Z",
                "message": {"role": "user", "content": "b"},
            }),
        ]
        jsonl = tmp_path / "exact.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert len(session.work_blocks) == 1

    def test_just_over_30min_splits(self, tmp_path):
        """Gap of 30 min + 1 sec should split."""
        lines = [
            json.dumps({
                "type": "user",
                "sessionId": "sess-over",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T10:00:00.000Z",
                "message": {"role": "user", "content": "a"},
            }),
            json.dumps({
                "type": "user",
                "sessionId": "sess-over",
                "uuid": "m2",
                "parentUuid": "m1",
                "timestamp": "2026-01-01T10:30:01.000Z",
                "message": {"role": "user", "content": "b"},
            }),
        ]
        jsonl = tmp_path / "over.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert len(session.work_blocks) == 2

    def test_single_message_session(self, tmp_path):
        """Session with 1 message -> 1 block with 0 duration."""
        lines = [
            json.dumps({
                "type": "user",
                "sessionId": "sess-single",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T10:00:00.000Z",
                "message": {"role": "user", "content": "solo"},
            }),
        ]
        jsonl = tmp_path / "single.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert len(session.work_blocks) == 1
        assert session.work_blocks[0].duration_seconds == 0
        assert session.work_blocks[0].message_count == 1
        assert session.active_duration_seconds == 0

    def test_three_blocks_from_two_gaps(self, tmp_path):
        """Two 1-hour gaps -> 3 blocks."""
        lines = [
            json.dumps({
                "type": "user",
                "sessionId": "sess-3b",
                "uuid": "m1",
                "parentUuid": None,
                "timestamp": "2026-01-01T09:00:00.000Z",
                "message": {"role": "user", "content": "a"},
            }),
            json.dumps({
                "type": "user",
                "sessionId": "sess-3b",
                "uuid": "m2",
                "parentUuid": "m1",
                "timestamp": "2026-01-01T10:00:01.000Z",
                "message": {"role": "user", "content": "b"},
            }),
            json.dumps({
                "type": "user",
                "sessionId": "sess-3b",
                "uuid": "m3",
                "parentUuid": "m2",
                "timestamp": "2026-01-01T11:00:02.000Z",
                "message": {"role": "user", "content": "c"},
            }),
        ]
        jsonl = tmp_path / "3blocks.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")

        session = parse_jsonl_file(jsonl)[0]
        assert len(session.work_blocks) == 3
        total_msgs = sum(b.message_count for b in session.work_blocks)
        assert total_msgs == session.message_count
