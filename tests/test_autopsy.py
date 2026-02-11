"""Comprehensive tests for the session autopsy feature."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from click.testing import CliRunner

from aide.autopsy.analyzer import (
    CacheEfficiency,
    analyze_context,
    analyze_cost,
    analyze_suggestions,
    analyze_summary,
)
from aide.autopsy.report import _format_duration, render_report
from aide.autopsy.suggestions import generate_suggestions
from aide.cli import cli
from aide.db import ingest_sessions, init_db
from aide.models import ParsedMessage, ParsedSession, ToolCall

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_test_session(
    session_id="test-session-001",
    project_name="myproject",
    started_at="2026-02-01T10:00:00",
    ended_at="2026-02-01T10:30:00",
    duration_seconds=1800,
    message_count=8,
    user_message_count=4,
    assistant_message_count=4,
    tool_call_count=6,
    estimated_cost_usd=0.15,
    total_input_tokens=9000,
    total_output_tokens=800,
    total_cache_read_tokens=15500,
    total_cache_creation_tokens=3000,
    file_read_count=2,
    file_write_count=0,
    file_edit_count=1,
    bash_count=1,
) -> dict:
    """Build a session dict matching DB row shape."""
    return {
        "id": 1,
        "session_id": session_id,
        "project_path": f"-Users-brian-projects-{project_name}",
        "project_name": project_name,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds,
        "message_count": message_count,
        "user_message_count": user_message_count,
        "assistant_message_count": assistant_message_count,
        "tool_call_count": tool_call_count,
        "estimated_cost_usd": estimated_cost_usd,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cache_read_tokens": total_cache_read_tokens,
        "total_cache_creation_tokens": total_cache_creation_tokens,
        "file_read_count": file_read_count,
        "file_write_count": file_write_count,
        "file_edit_count": file_edit_count,
        "bash_count": bash_count,
    }


def _make_test_messages(
    session_id="test-session-001",
    growing_input=True,
) -> list[dict]:
    """Build a list of message dicts with growing input_tokens.

    Returns 8 messages: 4 user + 4 assistant alternating.
    Assistant messages have growing input_tokens by default.
    """
    messages = []
    base_time = "2026-02-01T10:00:"

    # user 1
    messages.append({
        "id": 1, "session_id": session_id, "message_uuid": "msg-u1",
        "role": "user", "type": "user", "timestamp": f"{base_time}01",
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "content_length": 50, "has_tool_use": 0, "tool_names": None,
    })
    # assistant 1 — Read
    messages.append({
        "id": 2, "session_id": session_id, "message_uuid": "msg-a1",
        "role": "assistant", "type": "assistant", "timestamp": f"{base_time}05",
        "input_tokens": 1500 if growing_input else 1500,
        "output_tokens": 200,
        "cache_read_tokens": 500, "cache_creation_tokens": 3000,
        "content_length": 150, "has_tool_use": 1, "tool_names": "Read",
    })
    # user 2
    messages.append({
        "id": 3, "session_id": session_id, "message_uuid": "msg-u2",
        "role": "user", "type": "user", "timestamp": f"{base_time}06",
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "content_length": 30, "has_tool_use": 0, "tool_names": None,
    })
    # assistant 2 — Edit
    messages.append({
        "id": 4, "session_id": session_id, "message_uuid": "msg-a2",
        "role": "assistant", "type": "assistant", "timestamp": f"{base_time}15",
        "input_tokens": 2000 if growing_input else 2000,
        "output_tokens": 350,
        "cache_read_tokens": 3500, "cache_creation_tokens": 0,
        "content_length": 250, "has_tool_use": 1, "tool_names": "Edit",
    })
    # user 3
    messages.append({
        "id": 5, "session_id": session_id, "message_uuid": "msg-u3",
        "role": "user", "type": "user", "timestamp": f"{base_time}16",
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "content_length": 30, "has_tool_use": 0, "tool_names": None,
    })
    # assistant 3 — Bash
    messages.append({
        "id": 6, "session_id": session_id, "message_uuid": "msg-a3",
        "role": "assistant", "type": "assistant", "timestamp": f"{base_time}25",
        "input_tokens": 2500 if growing_input else 2500,
        "output_tokens": 150,
        "cache_read_tokens": 5500, "cache_creation_tokens": 0,
        "content_length": 100, "has_tool_use": 1, "tool_names": "Bash",
    })
    # user 4
    messages.append({
        "id": 7, "session_id": session_id, "message_uuid": "msg-u4",
        "role": "user", "type": "user", "timestamp": f"{base_time}30",
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "content_length": 30, "has_tool_use": 0, "tool_names": None,
    })
    # assistant 4 — no tools (final response)
    messages.append({
        "id": 8, "session_id": session_id, "message_uuid": "msg-a4",
        "role": "assistant", "type": "assistant", "timestamp": f"{base_time}35",
        "input_tokens": 3000 if growing_input else 3000,
        "output_tokens": 100,
        "cache_read_tokens": 6000, "cache_creation_tokens": 0,
        "content_length": 200, "has_tool_use": 0, "tool_names": None,
    })

    return messages


def _make_test_tool_calls(session_id="test-session-001") -> list[dict]:
    """Build tool_call dicts."""
    return [
        {"id": 1, "session_id": session_id, "message_uuid": "msg-a1",
         "tool_name": "Read", "file_path": "/src/parser.py",
         "timestamp": "2026-02-01T10:00:05"},
        {"id": 2, "session_id": session_id, "message_uuid": "msg-a2",
         "tool_name": "Edit", "file_path": "/src/parser.py",
         "timestamp": "2026-02-01T10:00:15"},
        {"id": 3, "session_id": session_id, "message_uuid": "msg-a3",
         "tool_name": "Bash", "file_path": None,
         "timestamp": "2026-02-01T10:00:25"},
    ]


def _make_test_tool_usage() -> list[dict]:
    """Build tool usage aggregation."""
    return [
        {"tool_name": "Read", "count": 3},
        {"tool_name": "Edit", "count": 2},
        {"tool_name": "Bash", "count": 1},
    ]


def _make_test_files_touched() -> list[dict]:
    """Build file access aggregation."""
    return [
        {"file_path": "/src/parser.py", "read_count": 3,
         "edit_count": 2, "write_count": 0, "total": 5},
        {"file_path": "/src/main.py", "read_count": 1,
         "edit_count": 0, "write_count": 1, "total": 2},
        {"file_path": "/tests/test_parser.py", "read_count": 2,
         "edit_count": 0, "write_count": 0, "total": 2},
    ]


def _make_parsed_session(
    session_id="test-session-001",
    project_name="myproject",
) -> ParsedSession:
    """Build a ParsedSession for DB ingestion in integration tests."""
    started = datetime(2026, 2, 1, 10, 0, 0, tzinfo=timezone.utc)
    ended = datetime(2026, 2, 1, 10, 30, 0, tzinfo=timezone.utc)
    mid1 = datetime(2026, 2, 1, 10, 5, 0, tzinfo=timezone.utc)
    mid2 = datetime(2026, 2, 1, 10, 15, 0, tzinfo=timezone.utc)
    mid3 = datetime(2026, 2, 1, 10, 25, 0, tzinfo=timezone.utc)
    mid4 = datetime(2026, 2, 1, 10, 28, 0, tzinfo=timezone.utc)

    messages = [
        ParsedMessage(
            uuid="msg-u1", parent_uuid=None, session_id=session_id,
            timestamp=started, role="user", type="user",
            input_tokens=0, output_tokens=0,
            cache_read_tokens=0, cache_creation_tokens=0,
            content_length=50,
        ),
        ParsedMessage(
            uuid="msg-a1", parent_uuid="msg-u1", session_id=session_id,
            timestamp=mid1, role="assistant", type="assistant",
            input_tokens=1500, output_tokens=200,
            cache_read_tokens=500, cache_creation_tokens=3000,
            content_length=150,
            tool_calls=[
                ToolCall(tool_name="Read", file_path="/src/parser.py", timestamp=mid1),
                ToolCall(tool_name="Read", file_path="/src/parser.py", timestamp=mid1),
                ToolCall(tool_name="Read", file_path="/src/main.py", timestamp=mid1),
            ],
        ),
        ParsedMessage(
            uuid="msg-u2", parent_uuid="msg-a1", session_id=session_id,
            timestamp=mid2, role="user", type="user",
            input_tokens=0, output_tokens=0,
            cache_read_tokens=0, cache_creation_tokens=0,
            content_length=30,
        ),
        ParsedMessage(
            uuid="msg-a2", parent_uuid="msg-u2", session_id=session_id,
            timestamp=mid2, role="assistant", type="assistant",
            input_tokens=2000, output_tokens=350,
            cache_read_tokens=3500, cache_creation_tokens=0,
            content_length=250,
            tool_calls=[
                ToolCall(tool_name="Edit", file_path="/src/parser.py", timestamp=mid2),
            ],
        ),
        ParsedMessage(
            uuid="msg-u3", parent_uuid="msg-a2", session_id=session_id,
            timestamp=mid3, role="user", type="user",
            input_tokens=0, output_tokens=0,
            cache_read_tokens=0, cache_creation_tokens=0,
            content_length=30,
        ),
        ParsedMessage(
            uuid="msg-a3", parent_uuid="msg-u3", session_id=session_id,
            timestamp=mid3, role="assistant", type="assistant",
            input_tokens=2500, output_tokens=150,
            cache_read_tokens=5500, cache_creation_tokens=0,
            content_length=100,
            tool_calls=[
                ToolCall(tool_name="Bash", file_path=None, timestamp=mid3),
            ],
        ),
        ParsedMessage(
            uuid="msg-u4", parent_uuid="msg-a3", session_id=session_id,
            timestamp=mid4, role="user", type="user",
            input_tokens=0, output_tokens=0,
            cache_read_tokens=0, cache_creation_tokens=0,
            content_length=30,
        ),
        ParsedMessage(
            uuid="msg-a4", parent_uuid="msg-u4", session_id=session_id,
            timestamp=mid4, role="assistant", type="assistant",
            input_tokens=3000, output_tokens=100,
            cache_read_tokens=6000, cache_creation_tokens=0,
            content_length=200,
        ),
    ]

    return ParsedSession(
        session_id=session_id,
        project_path=f"-Users-brian-projects-{project_name}",
        project_name=project_name,
        source_file="/logs/test.jsonl",
        started_at=started,
        ended_at=ended,
        messages=messages,
        duration_seconds=1800,
        total_input_tokens=9000,
        total_output_tokens=800,
        total_cache_read_tokens=15500,
        total_cache_creation_tokens=3000,
        estimated_cost_usd=0.15,
        message_count=8,
        user_message_count=4,
        assistant_message_count=4,
        tool_call_count=5,
        file_read_count=3,
        file_write_count=0,
        file_edit_count=1,
        bash_count=1,
    )


# ---------------------------------------------------------------------------
# TestQueries — integration tests
# ---------------------------------------------------------------------------


class TestQueries:
    """Integration tests for autopsy query functions using a real SQLite DB."""

    def test_get_session_returns_data(self, tmp_db):
        """get_session returns session dict for valid ID."""
        from aide.autopsy.queries import get_session

        init_db(tmp_db)
        parsed = _make_parsed_session()
        ingest_sessions(tmp_db, [parsed])

        result = get_session(tmp_db, "test-session-001")
        assert result is not None
        assert result["session_id"] == "test-session-001"
        assert result["project_name"] == "myproject"
        assert result["total_input_tokens"] == 9000

    def test_get_session_returns_none_for_missing(self, tmp_db):
        """get_session returns None for non-existent session."""
        from aide.autopsy.queries import get_session

        init_db(tmp_db)
        result = get_session(tmp_db, "nonexistent-id")
        assert result is None

    def test_get_session_messages_ordered(self, tmp_db):
        """get_session_messages returns messages ordered by timestamp."""
        from aide.autopsy.queries import get_session_messages

        init_db(tmp_db)
        parsed = _make_parsed_session()
        ingest_sessions(tmp_db, [parsed])

        messages = get_session_messages(tmp_db, "test-session-001")
        assert len(messages) == 8
        # Verify ordering
        timestamps = [m["timestamp"] for m in messages]
        assert timestamps == sorted(timestamps)
        # First is user, second is assistant
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

    def test_get_session_tool_calls(self, tmp_db):
        """get_session_tool_calls returns all tool calls."""
        from aide.autopsy.queries import get_session_tool_calls

        init_db(tmp_db)
        parsed = _make_parsed_session()
        ingest_sessions(tmp_db, [parsed])

        tool_calls = get_session_tool_calls(tmp_db, "test-session-001")
        assert len(tool_calls) == 5  # 3 Read + 1 Edit + 1 Bash
        tool_names = [tc["tool_name"] for tc in tool_calls]
        assert "Read" in tool_names
        assert "Edit" in tool_names
        assert "Bash" in tool_names

    def test_get_session_tool_usage(self, tmp_db):
        """get_session_tool_usage aggregates by tool name."""
        from aide.autopsy.queries import get_session_tool_usage

        init_db(tmp_db)
        parsed = _make_parsed_session()
        ingest_sessions(tmp_db, [parsed])

        usage = get_session_tool_usage(tmp_db, "test-session-001")
        usage_dict = {u["tool_name"]: u["count"] for u in usage}
        assert usage_dict["Read"] == 3
        assert usage_dict["Edit"] == 1
        assert usage_dict["Bash"] == 1
        # Sorted by count desc
        assert usage[0]["count"] >= usage[-1]["count"]

    def test_get_session_files_touched(self, tmp_db):
        """get_session_files_touched categorizes Read/Edit/Write correctly."""
        from aide.autopsy.queries import get_session_files_touched

        init_db(tmp_db)
        parsed = _make_parsed_session()
        ingest_sessions(tmp_db, [parsed])

        files = get_session_files_touched(tmp_db, "test-session-001")
        # Should have 2 files: /src/parser.py and /src/main.py
        # Bash has no file_path so it's excluded
        assert len(files) == 2

        files_dict = {f["file_path"]: f for f in files}
        parser = files_dict["/src/parser.py"]
        assert parser["read_count"] == 2  # 2 Read calls to parser.py
        assert parser["edit_count"] == 1
        assert parser["write_count"] == 0
        assert parser["total"] == 3

        main = files_dict["/src/main.py"]
        assert main["read_count"] == 1
        assert main["edit_count"] == 0

    def test_get_session_files_excludes_null_paths(self, tmp_db):
        """Files with NULL file_path (e.g. Bash) are excluded."""
        from aide.autopsy.queries import get_session_files_touched

        init_db(tmp_db)
        parsed = _make_parsed_session()
        ingest_sessions(tmp_db, [parsed])

        files = get_session_files_touched(tmp_db, "test-session-001")
        file_paths = [f["file_path"] for f in files]
        assert None not in file_paths


# ---------------------------------------------------------------------------
# TestAnalyzeSummary
# ---------------------------------------------------------------------------


class TestAnalyzeSummary:
    """Tests for analyze_summary."""

    def test_all_fields_populated(self):
        """All summary fields are populated from session dict."""
        session = _make_test_session()
        tool_usage = _make_test_tool_usage()
        files_touched = _make_test_files_touched()

        result = analyze_summary(session, tool_usage, files_touched)

        assert result.session_id == "test-session-001"
        assert result.project_name == "myproject"
        assert result.started_at == "2026-02-01T10:00:00"
        assert result.ended_at == "2026-02-01T10:30:00"
        assert result.duration_seconds == 1800
        assert result.message_count == 8
        assert result.user_message_count == 4
        assert result.assistant_message_count == 4
        assert result.tool_call_count == 6
        assert result.estimated_cost_usd == 0.15
        assert result.total_input_tokens == 9000
        assert result.total_output_tokens == 800

    def test_files_modified(self):
        """files_modified includes files with edit_count or write_count > 0."""
        session = _make_test_session()
        files_touched = _make_test_files_touched()

        result = analyze_summary(session, _make_test_tool_usage(), files_touched)

        assert "/src/parser.py" in result.files_modified  # edit_count=2
        assert "/src/main.py" in result.files_modified  # write_count=1
        assert "/tests/test_parser.py" not in result.files_modified  # only reads

    def test_files_read(self):
        """files_read includes files with read_count > 0."""
        session = _make_test_session()
        files_touched = _make_test_files_touched()

        result = analyze_summary(session, _make_test_tool_usage(), files_touched)

        assert "/src/parser.py" in result.files_read  # read_count=3
        assert "/src/main.py" in result.files_read  # read_count=1
        assert "/tests/test_parser.py" in result.files_read  # read_count=2

    def test_tool_breakdown(self):
        """tool_breakdown matches input tool_usage."""
        session = _make_test_session()
        tool_usage = _make_test_tool_usage()

        result = analyze_summary(session, tool_usage, [])

        breakdown_dict = {t.tool_name: t.count for t in result.tool_breakdown}
        assert breakdown_dict == {"Read": 3, "Edit": 2, "Bash": 1}

    def test_empty_files_touched(self):
        """Empty files_touched produces empty modified/read lists."""
        session = _make_test_session()

        result = analyze_summary(session, [], [])

        assert result.files_modified == []
        assert result.files_read == []


# ---------------------------------------------------------------------------
# TestAnalyzeCost
# ---------------------------------------------------------------------------


class TestAnalyzeCost:
    """Tests for analyze_cost."""

    def test_read_tool_categorized_as_file_reads(self):
        """Messages with Read tools go to file_reads category."""
        session = _make_test_session()
        messages = _make_test_messages()
        tool_calls = _make_test_tool_calls()

        result = analyze_cost(session, messages, tool_calls)

        cat_dict = {c.category: c for c in result.categories}
        assert cat_dict["file_reads"].input_tokens > 0

    def test_edit_tool_categorized_as_code_generation(self):
        """Messages with Edit tools go to code_generation category."""
        session = _make_test_session()
        messages = _make_test_messages()
        tool_calls = _make_test_tool_calls()

        result = analyze_cost(session, messages, tool_calls)

        cat_dict = {c.category: c for c in result.categories}
        assert cat_dict["code_generation"].input_tokens > 0

    def test_system_user_messages_categorized_as_overhead(self):
        """User/system messages go to system_overhead."""
        session = _make_test_session()
        messages = _make_test_messages()
        tool_calls = _make_test_tool_calls()

        result = analyze_cost(session, messages, tool_calls)

        cat_dict = {c.category: c for c in result.categories}
        # 4 user messages + 1 assistant without tools = system_overhead
        assert "system_overhead" in cat_dict

    def test_category_percentages_sum_to_100(self):
        """Category percentages approximately sum to 100%."""
        session = _make_test_session()
        messages = _make_test_messages()
        tool_calls = _make_test_tool_calls()

        result = analyze_cost(session, messages, tool_calls)

        total_pct = sum(c.percentage for c in result.categories)
        # Allow small floating-point drift
        assert abs(total_pct - 100.0) < 5.0

    def test_most_expensive_turns_sorted_desc(self):
        """Most expensive turns are sorted by cost descending."""
        session = _make_test_session()
        messages = _make_test_messages()
        tool_calls = _make_test_tool_calls()

        result = analyze_cost(session, messages, tool_calls)

        costs = [t.estimated_cost_usd for t in result.most_expensive_turns]
        assert costs == sorted(costs, reverse=True)

    def test_most_expensive_turns_max_5(self):
        """At most 5 expensive turns returned."""
        session = _make_test_session()
        messages = _make_test_messages()
        tool_calls = _make_test_tool_calls()

        result = analyze_cost(session, messages, tool_calls)

        assert len(result.most_expensive_turns) <= 5

    def test_cache_efficiency_calculation(self):
        """Cache efficiency fields calculated correctly."""
        session = _make_test_session(
            total_input_tokens=9000,
            total_cache_read_tokens=15500,
            total_cache_creation_tokens=3000,
        )
        messages = _make_test_messages()
        tool_calls = _make_test_tool_calls()

        result = analyze_cost(session, messages, tool_calls)

        ce = result.cache_efficiency
        assert ce.total_input_tokens == 9000
        assert ce.cache_read_tokens == 15500
        assert ce.cache_creation_tokens == 3000
        assert ce.fresh_input_tokens == 9000

        # cache_hit_rate = 15500 / (15500 + 9000 + 3000) = 15500 / 27500
        expected_rate = 15500 / (15500 + 9000 + 3000)
        assert abs(ce.cache_hit_rate - expected_rate) < 0.001

        # cache_savings = 15500 * (3.00 - 0.30) / 1_000_000
        expected_savings = 15500 * 2.70 / 1_000_000
        assert abs(ce.cache_savings_usd - expected_savings) < 0.0001

    def test_zero_cost_session(self):
        """Session with zero cost doesn't divide by zero."""
        session = _make_test_session(
            estimated_cost_usd=0.0,
            total_input_tokens=0,
            total_output_tokens=0,
            total_cache_read_tokens=0,
            total_cache_creation_tokens=0,
        )
        messages = []

        result = analyze_cost(session, messages, [])

        assert result.total_cost_usd == 0.0
        assert result.cache_efficiency.cache_hit_rate == 0.0


# ---------------------------------------------------------------------------
# TestAnalyzeContext
# ---------------------------------------------------------------------------


class TestAnalyzeContext:
    """Tests for analyze_context."""

    def test_growing_input_no_compaction(self):
        """Growing input_tokens → no compaction events."""
        messages = _make_test_messages()
        result = analyze_context(messages)

        assert result.estimated_compaction_count == 0
        assert result.compaction_events == []

    def test_compaction_detected(self):
        """Drop from 150K to 50K → compaction detected."""
        messages = [
            {"role": "assistant", "input_tokens": 50000, "type": "assistant",
             "timestamp": "2026-02-01T10:00:01"},
            {"role": "assistant", "input_tokens": 100000, "type": "assistant",
             "timestamp": "2026-02-01T10:00:02"},
            {"role": "assistant", "input_tokens": 150000, "type": "assistant",
             "timestamp": "2026-02-01T10:00:03"},
            {"role": "assistant", "input_tokens": 50000, "type": "assistant",
             "timestamp": "2026-02-01T10:00:04"},
        ]

        result = analyze_context(messages)

        assert result.estimated_compaction_count == 1
        assert len(result.compaction_events) == 1
        event = result.compaction_events[0]
        assert event.tokens_before == 150000
        assert event.tokens_after == 50000
        assert event.estimated_tokens_lost == 100000

    def test_peak_is_max(self):
        """peak = max total context (input + cache) across assistant messages."""
        messages = _make_test_messages()
        result = analyze_context(messages)

        # Assistant messages total context (input + cache_read + cache_creation):
        # 1500+500+3000=5000, 2000+3500+0=5500, 2500+5500+0=8000, 3000+6000+0=9000
        assert result.peak_context_tokens == 9000

    def test_context_utilization(self):
        """context_utilization_pct = peak / 200K."""
        messages = [
            {"role": "assistant", "input_tokens": 100000, "type": "assistant",
             "timestamp": "2026-02-01T10:00:01"},
        ]

        result = analyze_context(messages)

        assert abs(result.context_utilization_pct - 0.5) < 0.001

    def test_empty_messages_safe(self):
        """Empty messages list returns safe defaults."""
        result = analyze_context([])

        assert result.peak_context_tokens == 0
        assert result.estimated_compaction_count == 0
        assert result.compaction_events == []
        assert result.context_utilization_pct == 0.0
        assert result.avg_input_tokens_per_turn == 0

    def test_only_user_messages_no_context_curve(self):
        """Only user messages (no assistant) produce empty curve."""
        messages = [
            {"role": "user", "input_tokens": 0, "type": "user",
             "timestamp": "2026-02-01T10:00:01"},
        ]

        result = analyze_context(messages)

        assert result.context_curve == []
        assert result.peak_context_tokens == 0

    def test_avg_input_tokens(self):
        """avg_input_tokens_per_turn calculated correctly (includes cache)."""
        messages = _make_test_messages()
        result = analyze_context(messages)

        # Assistant messages total context: 5000, 5500, 8000, 9000 → avg = 6875
        assert result.avg_input_tokens_per_turn == 6875

    def test_no_compaction_when_previous_under_100k(self):
        """Drop below 50% doesn't trigger if previous < 100K."""
        messages = [
            {"role": "assistant", "input_tokens": 80000, "type": "assistant",
             "timestamp": "2026-02-01T10:00:01"},
            {"role": "assistant", "input_tokens": 30000, "type": "assistant",
             "timestamp": "2026-02-01T10:00:02"},
        ]

        result = analyze_context(messages)
        assert result.estimated_compaction_count == 0

    def test_multiple_compactions(self):
        """Multiple compaction events detected correctly."""
        messages = [
            {"role": "assistant", "input_tokens": 50000, "type": "assistant",
             "timestamp": "2026-02-01T10:00:01"},
            {"role": "assistant", "input_tokens": 120000, "type": "assistant",
             "timestamp": "2026-02-01T10:00:02"},
            {"role": "assistant", "input_tokens": 40000, "type": "assistant",
             "timestamp": "2026-02-01T10:00:03"},
            {"role": "assistant", "input_tokens": 130000, "type": "assistant",
             "timestamp": "2026-02-01T10:00:04"},
            {"role": "assistant", "input_tokens": 50000, "type": "assistant",
             "timestamp": "2026-02-01T10:00:05"},
        ]

        result = analyze_context(messages)
        assert result.estimated_compaction_count == 2


# ---------------------------------------------------------------------------
# TestSuggestions
# ---------------------------------------------------------------------------


class TestSuggestions:
    """Tests for the suggestions rules engine."""

    def test_file_read_3x_produces_high_suggestion(self):
        """File read 3+ times produces HIGH priority suggestion."""
        files = [{"file_path": "/src/a.py", "read_count": 3, "edit_count": 0,
                  "write_count": 0, "total": 3}]
        cache = CacheEfficiency(
            total_input_tokens=1000, cache_read_tokens=800,
            cache_creation_tokens=100, fresh_input_tokens=1000,
            cache_hit_rate=0.8, cache_savings_usd=0.01,
        )

        result = generate_suggestions(files, cache, 0, 10)

        high_suggestions = [s for s in result if s.priority == "high"]
        assert len(high_suggestions) == 1
        assert "/src/a.py" in high_suggestions[0].suggestion
        assert high_suggestions[0].category == "repeated_reads"

    def test_file_read_2x_no_suggestion(self):
        """File read only 2 times does NOT produce a suggestion."""
        files = [{"file_path": "/src/a.py", "read_count": 2, "edit_count": 0,
                  "write_count": 0, "total": 2}]
        cache = CacheEfficiency(
            total_input_tokens=1000, cache_read_tokens=800,
            cache_creation_tokens=100, fresh_input_tokens=1000,
            cache_hit_rate=0.8, cache_savings_usd=0.01,
        )

        result = generate_suggestions(files, cache, 0, 10)

        repeated_reads = [s for s in result if s.category == "repeated_reads"]
        assert len(repeated_reads) == 0

    def test_low_cache_hit_rate_medium_suggestion(self):
        """Cache hit rate < 50% produces MEDIUM suggestion."""
        cache = CacheEfficiency(
            total_input_tokens=1000, cache_read_tokens=300,
            cache_creation_tokens=100, fresh_input_tokens=1000,
            cache_hit_rate=0.40, cache_savings_usd=0.01,
        )

        result = generate_suggestions([], cache, 0, 10)

        cache_suggestions = [s for s in result if s.category == "cache_efficiency"]
        assert len(cache_suggestions) == 1
        assert cache_suggestions[0].priority == "medium"

    def test_high_cache_hit_rate_no_suggestion(self):
        """Cache hit rate >= 50% does NOT produce a suggestion."""
        cache = CacheEfficiency(
            total_input_tokens=1000, cache_read_tokens=800,
            cache_creation_tokens=100, fresh_input_tokens=1000,
            cache_hit_rate=0.60, cache_savings_usd=0.01,
        )

        result = generate_suggestions([], cache, 0, 10)

        cache_suggestions = [s for s in result if s.category == "cache_efficiency"]
        assert len(cache_suggestions) == 0

    def test_2_compactions_medium_suggestion(self):
        """2+ compaction events produce MEDIUM suggestion."""
        cache = CacheEfficiency(
            total_input_tokens=1000, cache_read_tokens=800,
            cache_creation_tokens=100, fresh_input_tokens=1000,
            cache_hit_rate=0.8, cache_savings_usd=0.01,
        )

        result = generate_suggestions([], cache, 2, 10)

        structure_suggestions = [s for s in result if s.category == "session_structure"]
        assert len(structure_suggestions) == 1
        assert structure_suggestions[0].priority == "medium"

    def test_1_compaction_no_suggestion(self):
        """Only 1 compaction does NOT produce a suggestion."""
        cache = CacheEfficiency(
            total_input_tokens=1000, cache_read_tokens=800,
            cache_creation_tokens=100, fresh_input_tokens=1000,
            cache_hit_rate=0.8, cache_savings_usd=0.01,
        )

        result = generate_suggestions([], cache, 1, 10)

        structure_suggestions = [s for s in result if s.category == "session_structure"]
        assert len(structure_suggestions) == 0

    def test_50_tool_calls_low_suggestion(self):
        """50+ tool calls produce LOW priority suggestion."""
        cache = CacheEfficiency(
            total_input_tokens=1000, cache_read_tokens=800,
            cache_creation_tokens=100, fresh_input_tokens=1000,
            cache_hit_rate=0.8, cache_savings_usd=0.01,
        )

        result = generate_suggestions([], cache, 0, 50)

        efficiency_suggestions = [s for s in result if s.category == "efficiency"]
        assert len(efficiency_suggestions) == 1
        assert efficiency_suggestions[0].priority == "low"

    def test_49_tool_calls_no_suggestion(self):
        """49 tool calls does NOT produce a suggestion."""
        cache = CacheEfficiency(
            total_input_tokens=1000, cache_read_tokens=800,
            cache_creation_tokens=100, fresh_input_tokens=1000,
            cache_hit_rate=0.8, cache_savings_usd=0.01,
        )

        result = generate_suggestions([], cache, 0, 49)

        efficiency_suggestions = [s for s in result if s.category == "efficiency"]
        assert len(efficiency_suggestions) == 0


# ---------------------------------------------------------------------------
# TestReport
# ---------------------------------------------------------------------------


class TestReport:
    """Tests for report rendering."""

    def _build_full_report(self):
        """Helper: run all analyzers and render a full report."""
        session = _make_test_session()
        tool_usage = _make_test_tool_usage()
        files_touched = _make_test_files_touched()
        messages = _make_test_messages()
        tool_calls = _make_test_tool_calls()

        summary = analyze_summary(session, tool_usage, files_touched)
        cost = analyze_cost(session, messages, tool_calls)
        context = analyze_context(messages)
        suggestions = analyze_suggestions(
            files_touched, cost.cache_efficiency,
            context.estimated_compaction_count, session["tool_call_count"],
        )
        return render_report(summary, cost, context, suggestions)

    def test_contains_all_section_headers(self):
        """Report contains all 4 section headers."""
        report = self._build_full_report()

        assert "## 1. Session Summary" in report
        assert "## 2. Cost Analysis" in report
        assert "## 3. Context Analysis" in report
        assert "## 4. CLAUDE.md Suggestions" in report

    def test_contains_session_id(self):
        """Report contains the session ID (first 8 chars)."""
        report = self._build_full_report()
        assert "test-ses" in report

    def test_contains_project_name(self):
        """Report contains the project name."""
        report = self._build_full_report()
        assert "myproject" in report

    def test_contains_footer(self):
        """Report contains the footer."""
        report = self._build_full_report()
        assert "Generated by aide autopsy" in report

    def test_ascii_chart_renders(self):
        """Report contains ASCII bar chart characters."""
        report = self._build_full_report()
        # Should contain block chars
        assert "\u2588" in report

    def test_empty_suggestions_message(self):
        """When no suggestions, report shows appropriate message."""
        session = _make_test_session(tool_call_count=5)
        # High cache hit rate, no repeated reads, no compactions
        summary = analyze_summary(session, [], [])
        cost = analyze_cost(session, _make_test_messages(), [])
        context = analyze_context(_make_test_messages())
        suggestions = analyze_suggestions(
            [], cost.cache_efficiency, 0, 5,
        )

        report = render_report(summary, cost, context, suggestions)
        assert "No suggestions" in report

    def test_format_duration_hours(self):
        """Duration formatting for hours."""
        assert _format_duration(7200) == "2h 0m"
        assert _format_duration(5400) == "1h 30m"

    def test_format_duration_minutes(self):
        """Duration formatting for minutes only."""
        assert _format_duration(300) == "5m"
        assert _format_duration(1800) == "30m"

    def test_format_duration_short(self):
        """Duration formatting for very short sessions."""
        assert _format_duration(30) == "<1m"
        assert _format_duration(0) == "\u2014"
        assert _format_duration(None) == "\u2014"


# ---------------------------------------------------------------------------
# TestCLI
# ---------------------------------------------------------------------------


class TestCLI:
    """Tests for the aide autopsy CLI command."""

    def test_autopsy_nonexistent_session(self, tmp_db):
        """aide autopsy with nonexistent session exits with error."""
        init_db(tmp_db)

        runner = CliRunner()
        with patch("aide.cli.load_config") as mock_config:
            from aide.config import AideConfig
            mock_config.return_value = AideConfig(
                subscription_user=False,
                log_dir=tmp_db.parent,
                port=8787,
                db_path=tmp_db,
            )
            result = runner.invoke(cli, ["autopsy", "nonexistent-id"])

        assert result.exit_code != 0
        assert "not found" in result.output or "not found" in (result.stderr or "")

    def test_autopsy_valid_session(self, tmp_db):
        """aide autopsy with valid session produces report."""
        init_db(tmp_db)
        parsed = _make_parsed_session()
        ingest_sessions(tmp_db, [parsed])

        runner = CliRunner()
        with patch("aide.cli.load_config") as mock_config:
            from aide.config import AideConfig
            mock_config.return_value = AideConfig(
                subscription_user=False,
                log_dir=tmp_db.parent,
                port=8787,
                db_path=tmp_db,
            )
            result = runner.invoke(cli, ["autopsy", "test-session-001"])

        assert result.exit_code == 0
        assert "Session Autopsy" in result.output
        assert "Session Summary" in result.output
        assert "Cost Analysis" in result.output
        assert "Context Analysis" in result.output
        assert "CLAUDE.md Suggestions" in result.output
        assert "Generated by aide autopsy" in result.output

    def test_autopsy_no_db(self, tmp_path):
        """aide autopsy with no database exits with error."""
        runner = CliRunner()
        with patch("aide.cli.load_config") as mock_config:
            from aide.config import AideConfig
            mock_config.return_value = AideConfig(
                subscription_user=False,
                log_dir=tmp_path,
                port=8787,
                db_path=tmp_path / "nonexistent.db",
            )
            result = runner.invoke(cli, ["autopsy", "any-id"])

        assert result.exit_code != 0
        assert "ingest" in result.output.lower() or "ingest" in (result.stderr or "").lower()
