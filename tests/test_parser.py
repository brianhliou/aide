"""Tests for the JSONL parser module."""

from __future__ import annotations

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
        import json

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
