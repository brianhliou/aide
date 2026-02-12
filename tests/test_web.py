"""Tests for the web dashboard — app factory, routes, and query functions."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aide.config import AideConfig
from aide.db import ingest_sessions, init_db, rebuild_daily_stats
from aide.models import ParsedMessage, ParsedSession, ToolCall, WorkBlock
from aide.web import queries
from aide.web.app import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_test_session(
    session_id="test-sess-1",
    project_name="test-project",
    cost=5.0,
    days_ago=0,
    input_tokens=1000,
    output_tokens=500,
    cache_read_tokens=200,
    cache_creation_tokens=100,
):
    """Build a ParsedSession with sensible defaults for web tests."""
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    msg = ParsedMessage(
        uuid=f"msg-{session_id}",
        parent_uuid=None,
        session_id=session_id,
        timestamp=now,
        role="assistant",
        type="assistant",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        content_length=500,
        tool_calls=[
            ToolCall(tool_name="Read", file_path="src/main.py", timestamp=now),
            ToolCall(tool_name="Edit", file_path="src/main.py", timestamp=now),
        ],
    )
    ended = now + timedelta(minutes=45)
    return ParsedSession(
        session_id=session_id,
        project_path=f"-Users-test-{project_name}",
        project_name=project_name,
        source_file=f"/fake/{session_id}.jsonl",
        started_at=now,
        ended_at=ended,
        messages=[msg],
        duration_seconds=2700,
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_cache_read_tokens=cache_read_tokens,
        total_cache_creation_tokens=cache_creation_tokens,
        estimated_cost_usd=cost,
        message_count=1,
        user_message_count=1,
        assistant_message_count=1,
        tool_call_count=2,
        file_read_count=1,
        file_write_count=0,
        file_edit_count=1,
        bash_count=0,
        compaction_count=0,
        peak_context_tokens=0,
        active_duration_seconds=2700,
        work_blocks=[
            WorkBlock(
                session_id=session_id,
                block_index=0,
                started_at=now,
                ended_at=ended,
                duration_seconds=2700,
                message_count=1,
            ),
        ],
    )


def _seed_db(db_path):
    """Seed db_path with two test sessions in two projects and rebuild daily stats."""
    init_db(db_path)
    s1 = _make_test_session(
        session_id="sess-alpha-1",
        project_name="alpha",
        cost=3.50,
        days_ago=0,
    )
    s2 = _make_test_session(
        session_id="sess-beta-1",
        project_name="beta",
        cost=7.25,
        days_ago=2,
    )
    ingest_sessions(db_path, [s1, s2])
    rebuild_daily_stats(db_path)


def _make_config(db_path, subscription_user=False):
    """Create an AideConfig pointing at the given db_path."""
    return AideConfig(
        subscription_user=subscription_user,
        log_dir=Path("/tmp/fake-logs"),
        port=8787,
        db_path=db_path,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path):
    """A temporary database pre-populated with test sessions."""
    db_path = tmp_path / "test-aide.db"
    _seed_db(db_path)
    return db_path


@pytest.fixture
def empty_db(tmp_path):
    """A temporary database with tables but no data."""
    db_path = tmp_path / "test-aide-empty.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def client(seeded_db):
    """Flask test client backed by a seeded database."""
    config = _make_config(seeded_db)
    app = create_app(config)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def empty_client(empty_db):
    """Flask test client backed by an empty database."""
    config = _make_config(empty_db)
    app = create_app(config)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def sub_client(seeded_db):
    """Flask test client for a subscription user."""
    config = _make_config(seeded_db, subscription_user=True)
    app = create_app(config)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ===========================================================================
# App Factory Tests
# ===========================================================================


class TestAppFactory:
    """Tests for create_app."""

    def test_create_app_returns_flask(self, seeded_db):
        config = _make_config(seeded_db)
        app = create_app(config)
        assert app is not None
        assert app.__class__.__name__ == "Flask"

    def test_config_values_set(self, seeded_db):
        config = _make_config(seeded_db, subscription_user=True)
        app = create_app(config)
        assert app.config["DB_PATH"] == seeded_db
        assert app.config["SUBSCRIPTION_USER"] is True

    def test_config_subscription_false(self, seeded_db):
        config = _make_config(seeded_db, subscription_user=False)
        app = create_app(config)
        assert app.config["SUBSCRIPTION_USER"] is False


# ===========================================================================
# Route Tests — all routes return 200 with test data
# ===========================================================================


class TestRoutes:
    """Test that each route renders successfully with seeded data."""

    def test_overview_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_overview_contains_title(self, client):
        resp = client.get("/")
        assert b"Overview" in resp.data

    def test_overview_contains_summary_sections(self, client):
        resp = client.get("/")
        assert b"Last 30 Days" in resp.data
        assert b"This Week" in resp.data
        assert b"Today" in resp.data

    def test_overview_contains_effectiveness_heading(self, client):
        resp = client.get("/")
        assert b"Effectiveness" in resp.data

    def test_overview_contains_effectiveness_cards(self, client):
        resp = client.get("/")
        assert b"Cache Hit Rate" in resp.data
        assert b"Edit Ratio" in resp.data
        assert b"Compaction Rate" in resp.data
        assert b"Read-to-Edit" in resp.data
        assert b"Turns / Prompt" in resp.data
        assert b"Error Rate" in resp.data
        assert b"Iteration Rate" in resp.data

    def test_overview_contains_chart_headings(self, client):
        resp = client.get("/")
        assert b"Cost Over Time" in resp.data
        assert b"Work Blocks Per Week" in resp.data
        assert b"Cost By Project" in resp.data
        assert b"Token Breakdown" in resp.data

    def test_projects_returns_200(self, client):
        resp = client.get("/projects")
        assert resp.status_code == 200

    def test_projects_contains_project_names(self, client):
        resp = client.get("/projects")
        assert b"alpha" in resp.data
        assert b"beta" in resp.data

    def test_projects_contains_table_headers(self, client):
        resp = client.get("/projects")
        assert b"Project" in resp.data
        assert b"Sessions" in resp.data
        assert b"Total Cost" in resp.data

    def test_sessions_returns_200(self, client):
        resp = client.get("/sessions")
        assert resp.status_code == 200

    def test_sessions_contains_session_ids(self, client):
        resp = client.get("/sessions")
        assert b"sess-alpha-1" in resp.data
        assert b"sess-beta-1" in resp.data

    def test_sessions_contains_table_headers(self, client):
        resp = client.get("/sessions")
        assert b"Date" in resp.data
        assert b"Project" in resp.data
        assert b"Cost" in resp.data

    def test_session_detail_returns_200(self, client):
        resp = client.get("/sessions/sess-alpha-1")
        assert resp.status_code == 200

    def test_session_detail_contains_project_name(self, client):
        resp = client.get("/sessions/sess-alpha-1")
        assert b"alpha" in resp.data

    def test_session_detail_contains_tool_usage(self, client):
        resp = client.get("/sessions/sess-alpha-1")
        assert b"Read" in resp.data
        assert b"Edit" in resp.data

    def test_session_detail_contains_files_touched(self, client):
        resp = client.get("/sessions/sess-alpha-1")
        assert b"src/main.py" in resp.data

    def test_session_detail_contains_token_section(self, client):
        resp = client.get("/sessions/sess-alpha-1")
        assert b"Tokens" in resp.data
        assert b"Input" in resp.data
        assert b"Output" in resp.data

    def test_tools_returns_200(self, client):
        resp = client.get("/tools")
        assert resp.status_code == 200

    def test_tools_contains_tool_names(self, client):
        resp = client.get("/tools")
        assert b"Read" in resp.data
        assert b"Edit" in resp.data

    def test_tools_contains_top_files_heading(self, client):
        resp = client.get("/tools")
        assert b"Most Accessed Files" in resp.data


# ===========================================================================
# Navigation
# ===========================================================================


class TestNavigation:
    """Test that navigation links are present on all pages."""

    @pytest.mark.parametrize("path", ["/", "/projects", "/sessions", "/tools"])
    def test_nav_links_present(self, client, path):
        resp = client.get(path)
        assert b'href="/"' in resp.data
        assert b'href="/projects"' in resp.data
        assert b'href="/sessions"' in resp.data
        assert b'href="/tools"' in resp.data


# ===========================================================================
# Edge Cases
# ===========================================================================


class TestEdgeCases:
    """Edge case tests for routes and queries."""

    def test_session_detail_not_found_returns_404(self, client):
        resp = client.get("/sessions/nonexistent-id")
        assert resp.status_code == 404

    def test_session_detail_404_body(self, client):
        resp = client.get("/sessions/nonexistent-id")
        assert b"Session not found" in resp.data

    def test_sessions_project_filter(self, client):
        resp = client.get("/sessions?project=alpha")
        assert resp.status_code == 200
        assert b"sess-alpha-1" in resp.data
        assert b"sess-beta-1" not in resp.data

    def test_sessions_project_filter_shows_banner(self, client):
        resp = client.get("/sessions?project=alpha")
        assert b"alpha" in resp.data
        assert b"show all" in resp.data

    def test_sessions_invalid_project_filter(self, client):
        resp = client.get("/sessions?project=nonexistent")
        assert resp.status_code == 200
        assert b"No sessions found" in resp.data


class TestEmptyDatabase:
    """All routes return 200 (not 500) on an empty database."""

    def test_overview_empty(self, empty_client):
        resp = empty_client.get("/")
        assert resp.status_code == 200
        assert b"Effectiveness" in resp.data

    def test_projects_empty(self, empty_client):
        resp = empty_client.get("/projects")
        assert resp.status_code == 200

    def test_sessions_empty(self, empty_client):
        resp = empty_client.get("/sessions")
        assert resp.status_code == 200
        assert b"No sessions found" in resp.data

    def test_tools_empty(self, empty_client):
        resp = empty_client.get("/tools")
        assert resp.status_code == 200
        assert b"No file access data found" in resp.data


class TestSubscriptionUser:
    """Subscription user hides cost from overview, projects, and sessions."""

    def test_overview_hides_cost(self, sub_client):
        resp = sub_client.get("/")
        data = resp.data.decode()
        # Cost chart canvases should not be present
        assert 'id="costOverTimeChart"' not in data
        assert 'id="costByProjectChart"' not in data

    def test_projects_hides_cost_columns(self, sub_client):
        resp = sub_client.get("/projects")
        assert b"Total Cost" not in resp.data
        assert b"Avg Cost/Session" not in resp.data

    def test_sessions_hides_cost_column(self, sub_client):
        resp = sub_client.get("/sessions")
        # Cost column header should not be present
        data = resp.data.decode()
        # The "Cost" header should not appear in the thead
        assert ">Cost<" not in data

    def test_session_detail_hides_cost(self, sub_client):
        resp = sub_client.get("/sessions/sess-alpha-1")
        data = resp.data.decode()
        assert ">Cost:<" not in data


# ===========================================================================
# Query Function Tests
# ===========================================================================


class TestQueryOverviewSummary:
    """Tests for get_overview_summary."""

    def test_returns_dict_with_expected_keys(self, seeded_db):
        result = queries.get_overview_summary(seeded_db)
        assert "last_30d" in result
        assert "this_week" in result
        assert "today" in result

    def test_last_30d_has_expected_fields(self, seeded_db):
        result = queries.get_overview_summary(seeded_db)
        last_30d = result["last_30d"]
        assert "sessions" in last_30d
        assert "work_blocks" in last_30d
        assert "cost" in last_30d
        assert "projects" in last_30d

    def test_last_30d_counts_sessions(self, seeded_db):
        result = queries.get_overview_summary(seeded_db)
        # Both test sessions are within the last 30 days
        assert result["last_30d"]["sessions"] == 2
        assert result["last_30d"]["projects"] == 2

    def test_empty_db_returns_zeros(self, empty_db):
        result = queries.get_overview_summary(empty_db)
        assert result["last_30d"]["sessions"] == 0
        assert result["last_30d"]["cost"] == 0
        assert result["today"]["sessions"] == 0


class TestQueryDailyCostSeries:
    """Tests for get_daily_cost_series."""

    def test_returns_list(self, seeded_db):
        result = queries.get_daily_cost_series(seeded_db)
        assert isinstance(result, list)

    def test_items_have_expected_keys(self, seeded_db):
        result = queries.get_daily_cost_series(seeded_db)
        if result:
            item = result[0]
            assert "date" in item
            assert "cost" in item
            assert "cost_7d_avg" in item

    def test_empty_db_returns_empty_list(self, empty_db):
        result = queries.get_daily_cost_series(empty_db)
        assert result == []


class TestQueryWeeklySessionCounts:
    """Tests for get_weekly_session_counts."""

    def test_returns_list(self, seeded_db):
        result = queries.get_weekly_session_counts(seeded_db)
        assert isinstance(result, list)

    def test_items_have_expected_keys(self, seeded_db):
        result = queries.get_weekly_session_counts(seeded_db)
        if result:
            item = result[0]
            assert "week_start" in item
            assert "session_count" in item

    def test_empty_db_returns_empty_list(self, empty_db):
        result = queries.get_weekly_session_counts(empty_db)
        assert result == []


class TestQueryCostByProject:
    """Tests for get_cost_by_project."""

    def test_returns_list(self, seeded_db):
        result = queries.get_cost_by_project(seeded_db)
        assert isinstance(result, list)

    def test_contains_both_projects(self, seeded_db):
        result = queries.get_cost_by_project(seeded_db)
        names = {r["project_name"] for r in result}
        assert names == {"alpha", "beta"}

    def test_costs_are_correct(self, seeded_db):
        result = queries.get_cost_by_project(seeded_db)
        by_name = {r["project_name"]: r["total_cost"] for r in result}
        assert abs(by_name["alpha"] - 3.50) < 1e-9
        assert abs(by_name["beta"] - 7.25) < 1e-9

    def test_sorted_by_cost_descending(self, seeded_db):
        result = queries.get_cost_by_project(seeded_db)
        costs = [r["total_cost"] for r in result]
        assert costs == sorted(costs, reverse=True)

    def test_empty_db_returns_empty_list(self, empty_db):
        result = queries.get_cost_by_project(empty_db)
        assert result == []


class TestQueryTokenBreakdown:
    """Tests for get_token_breakdown."""

    def test_returns_dict_with_expected_keys(self, seeded_db):
        result = queries.get_token_breakdown(seeded_db)
        assert "input" in result
        assert "output" in result
        assert "cache_read" in result
        assert "cache_creation" in result

    def test_values_are_summed(self, seeded_db):
        result = queries.get_token_breakdown(seeded_db)
        # Two sessions, each with 1000 input tokens
        assert result["input"] == 2000
        assert result["output"] == 1000
        assert result["cache_read"] == 400
        assert result["cache_creation"] == 200

    def test_empty_db_returns_zeros(self, empty_db):
        result = queries.get_token_breakdown(empty_db)
        assert result["input"] == 0
        assert result["output"] == 0
        assert result["cache_read"] == 0
        assert result["cache_creation"] == 0


class TestQueryProjectsTable:
    """Tests for get_projects_table."""

    def test_returns_list(self, seeded_db):
        result = queries.get_projects_table(seeded_db)
        assert isinstance(result, list)

    def test_items_have_expected_keys(self, seeded_db):
        result = queries.get_projects_table(seeded_db)
        item = result[0]
        assert "project_name" in item
        assert "session_count" in item
        assert "total_cost" in item
        assert "avg_cost_per_session" in item
        assert "total_duration_seconds" in item
        assert "total_tokens" in item

    def test_session_count_per_project(self, seeded_db):
        result = queries.get_projects_table(seeded_db)
        by_name = {r["project_name"]: r for r in result}
        assert by_name["alpha"]["session_count"] == 1
        assert by_name["beta"]["session_count"] == 1

    def test_empty_db_returns_empty_list(self, empty_db):
        result = queries.get_projects_table(empty_db)
        assert result == []


class TestQuerySessionScatterData:
    """Tests for get_session_scatter_data."""

    def test_returns_list(self, seeded_db):
        result = queries.get_session_scatter_data(seeded_db)
        assert isinstance(result, list)

    def test_items_have_expected_keys(self, seeded_db):
        result = queries.get_session_scatter_data(seeded_db)
        item = result[0]
        assert "session_id" in item
        assert "project_name" in item
        assert "estimated_cost_usd" in item
        assert "started_at" in item

    def test_contains_both_sessions(self, seeded_db):
        result = queries.get_session_scatter_data(seeded_db)
        ids = {r["session_id"] for r in result}
        assert ids == {"sess-alpha-1", "sess-beta-1"}

    def test_empty_db_returns_empty_list(self, empty_db):
        result = queries.get_session_scatter_data(empty_db)
        assert result == []


class TestQuerySessionsList:
    """Tests for get_sessions_list."""

    def test_returns_all_sessions(self, seeded_db):
        result = queries.get_sessions_list(seeded_db)
        assert len(result) == 2

    def test_items_have_expected_keys(self, seeded_db):
        result = queries.get_sessions_list(seeded_db)
        item = result[0]
        assert "session_id" in item
        assert "project_name" in item
        assert "started_at" in item
        assert "duration_seconds" in item
        assert "active_duration_seconds" in item
        assert "work_block_count" in item
        assert "message_count" in item
        assert "tool_call_count" in item
        assert "estimated_cost_usd" in item

    def test_filter_by_project(self, seeded_db):
        result = queries.get_sessions_list(seeded_db, project_name="alpha")
        assert len(result) == 1
        assert result[0]["session_id"] == "sess-alpha-1"

    def test_filter_nonexistent_project(self, seeded_db):
        result = queries.get_sessions_list(seeded_db, project_name="nonexistent")
        assert result == []

    def test_sorted_by_started_at_desc(self, seeded_db):
        result = queries.get_sessions_list(seeded_db)
        dates = [r["started_at"] for r in result]
        assert dates == sorted(dates, reverse=True)

    def test_empty_db_returns_empty_list(self, empty_db):
        result = queries.get_sessions_list(empty_db)
        assert result == []


class TestQuerySessionDetail:
    """Tests for get_session_detail."""

    def test_returns_dict_for_existing_session(self, seeded_db):
        result = queries.get_session_detail(seeded_db, "sess-alpha-1")
        assert result is not None
        assert isinstance(result, dict)

    def test_returns_none_for_missing_session(self, seeded_db):
        result = queries.get_session_detail(seeded_db, "nonexistent")
        assert result is None

    def test_contains_session_fields(self, seeded_db):
        result = queries.get_session_detail(seeded_db, "sess-alpha-1")
        assert result["session_id"] == "sess-alpha-1"
        assert result["project_name"] == "alpha"
        assert abs(result["estimated_cost_usd"] - 3.50) < 1e-9

    def test_contains_tool_usage(self, seeded_db):
        result = queries.get_session_detail(seeded_db, "sess-alpha-1")
        assert "tool_usage" in result
        tool_names = {t["tool_name"] for t in result["tool_usage"]}
        assert tool_names == {"Read", "Edit"}

    def test_tool_usage_has_counts(self, seeded_db):
        result = queries.get_session_detail(seeded_db, "sess-alpha-1")
        for tool in result["tool_usage"]:
            assert "count" in tool
            assert tool["count"] >= 1

    def test_contains_files_touched(self, seeded_db):
        result = queries.get_session_detail(seeded_db, "sess-alpha-1")
        assert "files_touched" in result
        assert len(result["files_touched"]) >= 1
        f = result["files_touched"][0]
        assert f["file_path"] == "src/main.py"
        assert "read_count" in f
        assert "edit_count" in f
        assert "write_count" in f
        assert "total" in f

    def test_empty_db_returns_none(self, empty_db):
        result = queries.get_session_detail(empty_db, "anything")
        assert result is None


class TestQueryEffectivenessSummary:
    """Tests for get_effectiveness_summary — metrics with error categorization."""

    def test_returns_dict_with_expected_keys(self, seeded_db):
        result = queries.get_effectiveness_summary(seeded_db)
        assert "cache_hit_rate" in result
        assert "edit_ratio" in result
        assert "compaction_rate" in result
        assert "read_to_edit_ratio" in result
        assert "output_ratio" in result
        assert "tokens_per_user_msg" in result
        assert "turns_per_user_prompt" in result
        assert "iteration_error_pct" in result
        assert "session_count" in result

    def test_session_count(self, seeded_db):
        result = queries.get_effectiveness_summary(seeded_db)
        assert result["session_count"] == 2

    def test_cache_hit_rate(self, seeded_db):
        result = queries.get_effectiveness_summary(seeded_db)
        # 2 sessions: each input=1000, cache_read=200, cache_creation=100
        # total: cache_read=400, denom = 2000+400+200 = 2600
        expected = 400 / 2600
        assert abs(result["cache_hit_rate"] - expected) < 1e-6

    def test_edit_ratio(self, seeded_db):
        result = queries.get_effectiveness_summary(seeded_db)
        # Each session: 1 Edit + 0 Write = 1, tool_call_count=2 → total: 2/4 = 0.5
        assert abs(result["edit_ratio"] - 0.5) < 1e-6

    def test_compaction_rate_zero(self, seeded_db):
        result = queries.get_effectiveness_summary(seeded_db)
        # Test sessions have compaction_count=0
        assert result["compaction_rate"] == 0.0

    def test_read_to_edit_ratio(self, seeded_db):
        result = queries.get_effectiveness_summary(seeded_db)
        # Each session: 1 Read, 1 Edit + 0 Write → total: 2 reads / 2 edits = 1.0
        assert abs(result["read_to_edit_ratio"] - 1.0) < 1e-6

    def test_output_ratio(self, seeded_db):
        result = queries.get_effectiveness_summary(seeded_db)
        # output=1000, input=2000, io_total=3000
        # output_ratio = 1000/3000
        expected = 1000 / 3000
        assert abs(result["output_ratio"] - expected) < 1e-6

    def test_tokens_per_user_msg(self, seeded_db):
        result = queries.get_effectiveness_summary(seeded_db)
        # all_tokens = (input + cache_read + cache_creation + output) * 2 sessions
        # = (1000 + 200 + 100 + 500) * 2 = 3600
        # user_message_count = 2 (1 per session)
        assert result["tokens_per_user_msg"] == 3600 // 2

    def test_turns_per_user_prompt(self, seeded_db):
        result = queries.get_effectiveness_summary(seeded_db)
        # assistant_count = 2, user_count = 2 → 1.0
        assert abs(result["turns_per_user_prompt"] - 1.0) < 1e-6

    def test_empty_db_returns_zeros(self, empty_db):
        result = queries.get_effectiveness_summary(empty_db)
        assert result["cache_hit_rate"] == 0.0
        assert result["edit_ratio"] == 0.0
        assert result["compaction_rate"] == 0.0
        assert result["tokens_per_user_msg"] == 0
        assert result["turns_per_user_prompt"] == 0.0
        assert result["session_count"] == 0


class TestQueryEffectivenessTrends:
    """Tests for get_effectiveness_trends — per-session trend data."""

    def test_returns_list(self, seeded_db):
        result = queries.get_effectiveness_trends(seeded_db)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_items_have_expected_keys(self, seeded_db):
        result = queries.get_effectiveness_trends(seeded_db)
        item = result[0]
        assert "date" in item
        assert "session_id" in item
        assert "cache_hit_rate" in item
        assert "edit_ratio" in item
        assert "had_compaction" in item

    def test_cache_hit_rate_per_session(self, seeded_db):
        result = queries.get_effectiveness_trends(seeded_db)
        # Each session: cache_read=200, input=1000, cache_creation=100
        # rate = 200 / (1000 + 200 + 100) = 200 / 1300
        expected = round(200 / 1300, 4)
        for item in result:
            assert abs(item["cache_hit_rate"] - expected) < 1e-4

    def test_edit_ratio_per_session(self, seeded_db):
        result = queries.get_effectiveness_trends(seeded_db)
        # Each session: 1 edit + 0 write = 1, tool_call_count=2 → 0.5
        for item in result:
            assert abs(item["edit_ratio"] - 0.5) < 1e-4

    def test_had_compaction_false(self, seeded_db):
        result = queries.get_effectiveness_trends(seeded_db)
        for item in result:
            assert item["had_compaction"] is False

    def test_sorted_by_date(self, seeded_db):
        result = queries.get_effectiveness_trends(seeded_db)
        dates = [r["date"] for r in result]
        assert dates == sorted(dates)

    def test_empty_db_returns_empty_list(self, empty_db):
        result = queries.get_effectiveness_trends(empty_db)
        assert result == []


class TestQueryToolCounts:
    """Tests for get_tool_counts."""

    def test_returns_list(self, seeded_db):
        result = queries.get_tool_counts(seeded_db)
        assert isinstance(result, list)

    def test_contains_tool_names(self, seeded_db):
        result = queries.get_tool_counts(seeded_db)
        names = {r["tool_name"] for r in result}
        assert "Read" in names
        assert "Edit" in names

    def test_counts_are_correct(self, seeded_db):
        result = queries.get_tool_counts(seeded_db)
        by_name = {r["tool_name"]: r["count"] for r in result}
        # 2 sessions, each with 1 Read and 1 Edit
        assert by_name["Read"] == 2
        assert by_name["Edit"] == 2

    def test_sorted_by_count_descending(self, seeded_db):
        result = queries.get_tool_counts(seeded_db)
        counts = [r["count"] for r in result]
        assert counts == sorted(counts, reverse=True)

    def test_empty_db_returns_empty_list(self, empty_db):
        result = queries.get_tool_counts(empty_db)
        assert result == []


class TestQueryToolWeekly:
    """Tests for get_tool_weekly."""

    def test_returns_list(self, seeded_db):
        result = queries.get_tool_weekly(seeded_db)
        assert isinstance(result, list)

    def test_items_have_expected_keys(self, seeded_db):
        result = queries.get_tool_weekly(seeded_db)
        if result:
            item = result[0]
            assert "week_start" in item
            assert "tool_name" in item
            assert "count" in item

    def test_empty_db_returns_empty_list(self, empty_db):
        result = queries.get_tool_weekly(empty_db)
        assert result == []


class TestQueryTopFiles:
    """Tests for get_top_files."""

    def test_returns_list(self, seeded_db):
        result = queries.get_top_files(seeded_db)
        assert isinstance(result, list)

    def test_items_have_expected_keys(self, seeded_db):
        result = queries.get_top_files(seeded_db)
        if result:
            item = result[0]
            assert "file_path" in item
            assert "read_count" in item
            assert "edit_count" in item
            assert "write_count" in item
            assert "total" in item

    def test_top_file_is_main(self, seeded_db):
        result = queries.get_top_files(seeded_db)
        assert result[0]["file_path"] == "src/main.py"
        # 2 reads + 2 edits across both sessions
        assert result[0]["read_count"] == 2
        assert result[0]["edit_count"] == 2
        assert result[0]["total"] == 4

    def test_limit_parameter(self, seeded_db):
        result = queries.get_top_files(seeded_db, limit=1)
        assert len(result) <= 1

    def test_empty_db_returns_empty_list(self, empty_db):
        result = queries.get_top_files(empty_db)
        assert result == []


# ===========================================================================
# Error Categorization Tests
# ===========================================================================


def _make_session_with_errors(
    session_id="err-sess-1",
    project_name="test-project",
):
    """Build a session with various error types for testing categorization."""
    now = datetime.now(timezone.utc)
    msg = ParsedMessage(
        uuid=f"msg-{session_id}",
        parent_uuid=None,
        session_id=session_id,
        timestamp=now,
        role="assistant",
        type="assistant",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        content_length=500,
        tool_calls=[
            ToolCall(
                tool_name="Bash", file_path=None, timestamp=now,
                command="uv run pytest tests/", is_error=True,
            ),
            ToolCall(
                tool_name="Bash", file_path=None, timestamp=now,
                command="uv run pytest tests/test_parser.py", is_error=True,
            ),
            ToolCall(
                tool_name="Bash", file_path=None, timestamp=now,
                command="uv run ruff check src/", is_error=True,
            ),
            ToolCall(
                tool_name="Edit", file_path="src/main.py", timestamp=now,
                is_error=True,
            ),
            ToolCall(
                tool_name="Read", file_path="nonexistent.py", timestamp=now,
                is_error=True,
            ),
            ToolCall(
                tool_name="Bash", file_path=None, timestamp=now,
                command="pip install foo", is_error=True,
            ),
            ToolCall(
                tool_name="Bash", file_path=None, timestamp=now,
                command="git push origin main", is_error=True,
            ),
            ToolCall(
                tool_name="Bash", file_path=None, timestamp=now,
                command="curl https://example.com", is_error=True,
            ),
            # Non-error calls
            ToolCall(tool_name="Read", file_path="src/main.py", timestamp=now),
            ToolCall(tool_name="Edit", file_path="src/main.py", timestamp=now),
        ],
    )
    ended = now + timedelta(minutes=30)
    return ParsedSession(
        session_id=session_id,
        project_path=f"-Users-test-{project_name}",
        project_name=project_name,
        source_file=f"/fake/{session_id}.jsonl",
        started_at=now,
        ended_at=ended,
        messages=[msg],
        duration_seconds=1800,
        total_input_tokens=1000,
        total_output_tokens=500,
        total_cache_read_tokens=0,
        total_cache_creation_tokens=0,
        estimated_cost_usd=2.0,
        message_count=1,
        user_message_count=1,
        assistant_message_count=1,
        tool_call_count=10,
        file_read_count=1,
        file_write_count=0,
        file_edit_count=1,
        bash_count=5,
        compaction_count=0,
        peak_context_tokens=0,
        tool_error_count=8,
        active_duration_seconds=1800,
        work_blocks=[
            WorkBlock(
                session_id=session_id,
                block_index=0,
                started_at=now,
                ended_at=ended,
                duration_seconds=1800,
                message_count=1,
            ),
        ],
    )


@pytest.fixture
def error_db(tmp_path):
    """A temporary database with error-producing sessions."""
    db_path = tmp_path / "test-errors.db"
    init_db(db_path)
    s = _make_session_with_errors()
    ingest_sessions(db_path, [s])
    rebuild_daily_stats(db_path)
    return db_path


class TestCategorizeError:
    """Tests for _categorize_error helper."""

    def test_edit_is_edit_mismatch(self):
        assert queries._categorize_error("Edit", None) == "Edit Mismatch"

    def test_read_is_file_access(self):
        assert queries._categorize_error("Read", None) == "File Access"

    def test_write_is_file_access(self):
        assert queries._categorize_error("Write", None) == "File Access"

    def test_bash_pytest_is_test(self):
        assert queries._categorize_error("Bash", "uv run pytest tests/") == "Test"

    def test_bash_ruff_is_lint(self):
        assert queries._categorize_error("Bash", "ruff check src/") == "Lint"

    def test_bash_mypy_is_lint(self):
        assert queries._categorize_error("Bash", "mypy src/") == "Lint"

    def test_bash_pip_is_build(self):
        assert queries._categorize_error("Bash", "pip install requests") == "Build"

    def test_bash_git_is_git(self):
        assert queries._categorize_error("Bash", "git push origin main") == "Git"

    def test_bash_unknown_is_other(self):
        assert queries._categorize_error("Bash", "curl https://example.com") == "Other"

    def test_non_bash_no_command_is_other(self):
        assert queries._categorize_error("Task", None) == "Other"


class TestErrorBreakdown:
    """Tests for get_error_breakdown."""

    def test_returns_list(self, error_db):
        result = queries.get_error_breakdown(error_db)
        assert isinstance(result, list)

    def test_has_expected_categories(self, error_db):
        result = queries.get_error_breakdown(error_db)
        cats = {r["category"] for r in result}
        assert "Test" in cats
        assert "Lint" in cats
        assert "Edit Mismatch" in cats

    def test_test_count_is_correct(self, error_db):
        result = queries.get_error_breakdown(error_db)
        by_cat = {r["category"]: r["count"] for r in result}
        assert by_cat["Test"] == 2  # two pytest commands

    def test_sorted_by_count_desc(self, error_db):
        result = queries.get_error_breakdown(error_db)
        counts = [r["count"] for r in result]
        assert counts == sorted(counts, reverse=True)

    def test_pct_sums_to_one(self, error_db):
        result = queries.get_error_breakdown(error_db)
        total_pct = sum(r["pct"] for r in result)
        assert abs(total_pct - 1.0) < 1e-6

    def test_empty_db_returns_empty(self, empty_db):
        result = queries.get_error_breakdown(empty_db)
        assert result == []


class TestSessionDetailErrorBreakdown:
    """Tests for error_breakdown in get_session_detail."""

    def test_session_detail_has_error_breakdown(self, error_db):
        result = queries.get_session_detail(error_db, "err-sess-1")
        assert "error_breakdown" in result
        assert len(result["error_breakdown"]) > 0

    def test_error_breakdown_has_category_and_count(self, error_db):
        result = queries.get_session_detail(error_db, "err-sess-1")
        for item in result["error_breakdown"]:
            assert "category" in item
            assert "count" in item

    def test_no_errors_returns_empty_breakdown(self, seeded_db):
        result = queries.get_session_detail(seeded_db, "sess-alpha-1")
        assert result["error_breakdown"] == []


class TestEffectivenessIterationPct:
    """Tests for iteration_error_pct in get_effectiveness_summary."""

    def test_iteration_pct_present(self, error_db):
        result = queries.get_effectiveness_summary(error_db)
        assert "iteration_error_pct" in result

    def test_iteration_pct_correct(self, error_db):
        result = queries.get_effectiveness_summary(error_db)
        # 8 errors: 2 test + 1 lint + 1 build = 4 iteration, 4 non-iteration
        # iteration_error_pct = 4/8 = 0.5
        # Actually: pytest(2) + ruff(1) + pip(1) = 4 iteration errors
        assert abs(result["iteration_error_pct"] - 0.5) < 1e-6

    def test_no_errors_returns_zero(self, seeded_db):
        result = queries.get_effectiveness_summary(seeded_db)
        assert result["iteration_error_pct"] == 0.0


# ===========================================================================
# Insights Query Tests
# ===========================================================================


class TestFirstPromptAnalysis:
    """Tests for get_first_prompt_analysis."""

    def test_returns_dict_with_expected_keys(self, seeded_db):
        result = queries.get_first_prompt_analysis(seeded_db)
        assert "buckets" in result
        assert "scatter" in result

    def test_scatter_returns_list(self, seeded_db):
        result = queries.get_first_prompt_analysis(seeded_db)
        assert isinstance(result["scatter"], list)
        # Test sessions only have assistant messages, so scatter may be empty
        if result["scatter"]:
            item = result["scatter"][0]
            assert "prompt_len" in item
            assert "cost" in item
            assert "errors" in item

    def test_empty_db_returns_empty(self, empty_db):
        result = queries.get_first_prompt_analysis(empty_db)
        assert result["buckets"] == []
        assert result["scatter"] == []


class TestCostConcentration:
    """Tests for get_cost_concentration."""

    def test_returns_dict_with_expected_keys(self, seeded_db):
        result = queries.get_cost_concentration(seeded_db)
        assert "sessions" in result
        assert "top3_pct" in result
        assert "median" in result
        assert "p90" in result

    def test_sessions_sorted_by_cost_desc(self, seeded_db):
        result = queries.get_cost_concentration(seeded_db)
        costs = [s["cost"] for s in result["sessions"]]
        assert costs == sorted(costs, reverse=True)

    def test_cumulative_pct_ends_at_one(self, seeded_db):
        result = queries.get_cost_concentration(seeded_db)
        if result["sessions"]:
            last_pct = result["sessions"][-1]["cumulative_pct"]
            assert abs(last_pct - 1.0) < 1e-6

    def test_empty_db_returns_zeros(self, empty_db):
        result = queries.get_cost_concentration(empty_db)
        assert result["sessions"] == []
        assert result["top3_pct"] == 0


class TestCostPerEditByDuration:
    """Tests for get_cost_per_edit_by_duration."""

    def test_returns_list(self, seeded_db):
        result = queries.get_cost_per_edit_by_duration(seeded_db)
        assert isinstance(result, list)

    def test_items_have_expected_keys(self, seeded_db):
        result = queries.get_cost_per_edit_by_duration(seeded_db)
        if result:
            item = result[0]
            assert "label" in item
            assert "n" in item
            assert "avg_cost_per_edit" in item

    def test_empty_db_returns_empty(self, empty_db):
        result = queries.get_cost_per_edit_by_duration(empty_db)
        assert result == []


class TestModelBreakdown:
    """Tests for get_model_breakdown."""

    def test_returns_list(self, seeded_db):
        result = queries.get_model_breakdown(seeded_db)
        assert isinstance(result, list)

    def test_empty_db_returns_empty(self, empty_db):
        result = queries.get_model_breakdown(empty_db)
        assert result == []


class TestToolSequences:
    """Tests for get_tool_sequences."""

    def test_returns_list(self, seeded_db):
        result = queries.get_tool_sequences(seeded_db)
        assert isinstance(result, list)

    def test_items_have_expected_keys(self, seeded_db):
        result = queries.get_tool_sequences(seeded_db)
        if result:
            item = result[0]
            assert "from_tool" in item
            assert "to_tool" in item
            assert "count" in item

    def test_limit_parameter(self, seeded_db):
        result = queries.get_tool_sequences(seeded_db, limit=1)
        assert len(result) <= 1

    def test_empty_db_returns_empty(self, empty_db):
        result = queries.get_tool_sequences(empty_db)
        assert result == []


class TestTimePatterns:
    """Tests for get_time_patterns."""

    def test_returns_dict_with_keys(self, seeded_db):
        result = queries.get_time_patterns(seeded_db)
        assert "by_hour" in result
        assert "by_day" in result

    def test_by_hour_items(self, seeded_db):
        result = queries.get_time_patterns(seeded_db)
        if result["by_hour"]:
            item = result["by_hour"][0]
            assert "hour" in item
            assert "count" in item

    def test_by_day_items(self, seeded_db):
        result = queries.get_time_patterns(seeded_db)
        if result["by_day"]:
            item = result["by_day"][0]
            assert "day_name" in item
            assert "count" in item

    def test_empty_db_returns_empty_lists(self, empty_db):
        result = queries.get_time_patterns(empty_db)
        assert result["by_hour"] == []
        assert result["by_day"] == []


class TestUserResponseTimes:
    """Tests for get_user_response_times."""

    def test_returns_dict_with_keys(self, seeded_db):
        result = queries.get_user_response_times(seeded_db)
        assert "median_seconds" in result
        assert "mean_seconds" in result
        assert "count" in result
        assert "buckets" in result

    def test_empty_db_returns_zeros(self, empty_db):
        result = queries.get_user_response_times(empty_db)
        assert result["count"] == 0
        assert result["buckets"] == []


class TestSessionDetailInsights:
    """Tests for file_focus_ratio and first_prompt_len in session detail."""

    def test_has_file_focus_ratio(self, seeded_db):
        result = queries.get_session_detail(seeded_db, "sess-alpha-1")
        assert "file_focus_ratio" in result
        assert "unique_files" in result

    def test_has_first_prompt_len(self, seeded_db):
        result = queries.get_session_detail(seeded_db, "sess-alpha-1")
        assert "first_prompt_len" in result


class TestEffectivenessCostPerEdit:
    """Test cost_per_edit in effectiveness summary."""

    def test_has_cost_per_edit(self, seeded_db):
        result = queries.get_effectiveness_summary(seeded_db)
        assert "cost_per_edit" in result

    def test_cost_per_edit_value(self, seeded_db):
        result = queries.get_effectiveness_summary(seeded_db)
        # 2 sessions: total cost = $10.75, total edits = 2 (1 edit each)
        expected = 10.75 / 2
        assert abs(result["cost_per_edit"] - expected) < 1e-6

    def test_empty_db_returns_zero(self, empty_db):
        result = queries.get_effectiveness_summary(empty_db)
        assert result["cost_per_edit"] == 0.0


# ===========================================================================
# Insights Route Tests
# ===========================================================================


class TestInsightsRoute:
    """Tests for the /insights route."""

    def test_insights_returns_200(self, client):
        resp = client.get("/insights")
        assert resp.status_code == 200

    def test_insights_contains_heading(self, client):
        resp = client.get("/insights")
        assert b"Insights" in resp.data

    def test_insights_contains_tool_sequences(self, client):
        resp = client.get("/insights")
        assert b"Tool Sequences" in resp.data

    def test_insights_empty_db_returns_200(self, empty_client):
        resp = empty_client.get("/insights")
        assert resp.status_code == 200


class TestInsightsNavigation:
    """Test that Insights nav link is present."""

    @pytest.mark.parametrize(
        "path", ["/", "/projects", "/sessions", "/tools", "/insights"],
    )
    def test_nav_insights_link(self, client, path):
        resp = client.get(path)
        assert b'href="/insights"' in resp.data


class TestToolsPageErrors:
    """Test that tools page renders error breakdown."""

    def test_tools_page_with_errors_returns_200(self, error_db):
        config = _make_config(error_db)
        app = create_app(config)
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/tools")
            assert resp.status_code == 200

    def test_tools_page_shows_error_breakdown_heading(self, error_db):
        config = _make_config(error_db)
        app = create_app(config)
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/tools")
            assert b"Error Breakdown" in resp.data

    def test_tools_page_shows_error_categories(self, error_db):
        config = _make_config(error_db)
        app = create_app(config)
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/tools")
            assert b"Test" in resp.data
            assert b"Edit Mismatch" in resp.data


# ===========================================================================
# Thinking Stats
# ===========================================================================


@pytest.fixture
def thinking_db(tmp_path):
    """Database with sessions that have thinking metrics."""
    db_path = tmp_path / "thinking.db"
    init_db(db_path)
    s1 = _make_test_session(session_id="think-1", project_name="alpha", cost=5.0)
    s1.total_thinking_chars = 50000
    s1.thinking_message_count = 10
    s2 = _make_test_session(session_id="think-2", project_name="beta", cost=3.0, days_ago=1)
    s2.total_thinking_chars = 0
    s2.thinking_message_count = 0
    ingest_sessions(db_path, [s1, s2])
    return db_path


class TestThinkingStats:
    def test_returns_dict(self, thinking_db):
        result = queries.get_thinking_stats(thinking_db)
        assert isinstance(result, dict)
        assert "sessions_with_thinking" in result
        assert "total_sessions" in result
        assert "avg_thinking_chars" in result
        assert "by_session" in result

    def test_counts_sessions_with_thinking(self, thinking_db):
        result = queries.get_thinking_stats(thinking_db)
        assert result["sessions_with_thinking"] == 1
        assert result["total_sessions"] == 2

    def test_avg_thinking_chars(self, thinking_db):
        result = queries.get_thinking_stats(thinking_db)
        assert result["avg_thinking_chars"] == 50000

    def test_by_session_details(self, thinking_db):
        result = queries.get_thinking_stats(thinking_db)
        assert len(result["by_session"]) == 1
        s = result["by_session"][0]
        assert s["session_id"] == "think-1"
        assert s["thinking_chars"] == 50000
        assert s["thinking_messages"] == 10

    def test_empty_db(self, empty_db):
        result = queries.get_thinking_stats(empty_db)
        assert result["sessions_with_thinking"] == 0
        assert result["avg_thinking_chars"] == 0
        assert result["by_session"] == []


# ===========================================================================
# Permission Mode Breakdown
# ===========================================================================


@pytest.fixture
def permission_db(tmp_path):
    """Database with sessions that have permission modes."""
    db_path = tmp_path / "permission.db"
    init_db(db_path)
    s1 = _make_test_session(session_id="perm-1", project_name="alpha")
    s1.permission_mode = "acceptEdits"
    s2 = _make_test_session(session_id="perm-2", project_name="beta", days_ago=1)
    s2.permission_mode = "acceptEdits"
    s3 = _make_test_session(session_id="perm-3", project_name="gamma", days_ago=2)
    s3.permission_mode = "default"
    ingest_sessions(db_path, [s1, s2, s3])
    return db_path


class TestPermissionModeBreakdown:
    def test_returns_list(self, permission_db):
        result = queries.get_permission_mode_breakdown(permission_db)
        assert isinstance(result, list)

    def test_modes_counted(self, permission_db):
        result = queries.get_permission_mode_breakdown(permission_db)
        modes = {r["mode"]: r for r in result}
        assert modes["acceptEdits"]["count"] == 2
        assert modes["default"]["count"] == 1

    def test_pct_sums_to_one(self, permission_db):
        result = queries.get_permission_mode_breakdown(permission_db)
        total = sum(r["pct"] for r in result)
        assert abs(total - 1.0) < 0.01

    def test_sorted_by_count_desc(self, permission_db):
        result = queries.get_permission_mode_breakdown(permission_db)
        counts = [r["count"] for r in result]
        assert counts == sorted(counts, reverse=True)

    def test_empty_db(self, empty_db):
        result = queries.get_permission_mode_breakdown(empty_db)
        assert result == []


# ===========================================================================
# Session Detail — Thinking + Permission
# ===========================================================================


class TestSessionDetailThinkingPermission:
    def test_thinking_ratio_in_detail(self, thinking_db):
        result = queries.get_session_detail(thinking_db, "think-1")
        assert "thinking_ratio" in result
        # 10 thinking messages / 1 assistant message
        assert result["thinking_ratio"] == 10.0

    def test_permission_mode_in_detail(self, permission_db):
        result = queries.get_session_detail(permission_db, "perm-1")
        assert result["permission_mode"] == "acceptEdits"

    def test_no_permission_mode(self, seeded_db):
        result = queries.get_session_detail(seeded_db, "sess-alpha-1")
        assert result["permission_mode"] is None


# ===========================================================================
# Insights Route — Thinking + Permission
# ===========================================================================


class TestInsightsThinkingPermission:
    def test_insights_with_thinking_renders(self, tmp_path):
        """Insights page renders with thinking data."""
        db_path = tmp_path / "insight-think.db"
        init_db(db_path)
        s = _make_test_session(session_id="it-1")
        s.total_thinking_chars = 80000
        s.thinking_message_count = 15
        s.permission_mode = "acceptEdits"
        ingest_sessions(db_path, [s])
        rebuild_daily_stats(db_path)

        config = _make_config(db_path)
        app = create_app(config)
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/insights")
            assert resp.status_code == 200
            assert b"Thinking Blocks" in resp.data
            assert b"Permission Modes" in resp.data


# ===========================================================================
# Work Blocks Tests
# ===========================================================================


class TestQueryWeeklyWorkBlockCounts:
    """Tests for get_weekly_work_block_counts."""

    def test_returns_list(self, seeded_db):
        result = queries.get_weekly_work_block_counts(seeded_db)
        assert isinstance(result, list)

    def test_items_have_expected_keys(self, seeded_db):
        result = queries.get_weekly_work_block_counts(seeded_db)
        if result:
            item = result[0]
            assert "week_start" in item
            assert "work_block_count" in item

    def test_empty_db_returns_empty_list(self, empty_db):
        result = queries.get_weekly_work_block_counts(empty_db)
        assert result == []

    def test_counts_work_blocks(self, seeded_db):
        result = queries.get_weekly_work_block_counts(seeded_db)
        total = sum(r["work_block_count"] for r in result)
        # 2 sessions, each with 1 work block
        assert total == 2


class TestSessionsListWorkBlocks:
    """Tests for work block fields in get_sessions_list."""

    def test_has_active_duration(self, seeded_db):
        result = queries.get_sessions_list(seeded_db)
        for item in result:
            assert "active_duration_seconds" in item

    def test_has_work_block_count(self, seeded_db):
        result = queries.get_sessions_list(seeded_db)
        for item in result:
            assert item["work_block_count"] == 1


class TestSessionDetailWorkBlocks:
    """Tests for work blocks in get_session_detail."""

    def test_has_work_blocks(self, seeded_db):
        result = queries.get_session_detail(seeded_db, "sess-alpha-1")
        assert "work_blocks" in result
        assert isinstance(result["work_blocks"], list)

    def test_work_block_fields(self, seeded_db):
        result = queries.get_session_detail(seeded_db, "sess-alpha-1")
        if result["work_blocks"]:
            wb = result["work_blocks"][0]
            assert "block_index" in wb
            assert "started_at" in wb
            assert "ended_at" in wb
            assert "duration_seconds" in wb
            assert "message_count" in wb

    def test_has_active_duration(self, seeded_db):
        result = queries.get_session_detail(seeded_db, "sess-alpha-1")
        assert result["active_duration_seconds"] == 2700


class TestOverviewWorkBlocks:
    """Tests for work block counts in overview summary."""

    def test_summary_has_work_blocks(self, seeded_db):
        result = queries.get_overview_summary(seeded_db)
        assert "work_blocks" in result["last_30d"]
        assert "work_blocks" in result["this_week"]
        assert "work_blocks" in result["today"]

    def test_work_block_counts(self, seeded_db):
        result = queries.get_overview_summary(seeded_db)
        assert result["last_30d"]["work_blocks"] == 2


class TestOverviewRouteWorkBlocks:
    """Test that overview page renders work block content."""

    def test_overview_shows_work_blocks(self, client):
        resp = client.get("/")
        assert b"work blocks" in resp.data

    def test_overview_shows_sessions_secondary(self, client):
        resp = client.get("/")
        assert b"sessions" in resp.data


class TestInsightsWorkBlocks:
    """Test that insights page uses work blocks for time patterns."""

    def test_insights_shows_work_blocks_by_hour(self, client):
        resp = client.get("/insights")
        assert b"Work Blocks by Hour" in resp.data

    def test_insights_shows_work_blocks_by_day(self, client):
        resp = client.get("/insights")
        assert b"Work Blocks by Day" in resp.data
