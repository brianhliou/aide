"""Tests for the web dashboard — app factory, routes, and query functions."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aide.config import AideConfig
from aide.db import ingest_sessions, init_db, rebuild_daily_stats
from aide.models import ParsedMessage, ParsedSession, ToolCall
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
    return ParsedSession(
        session_id=session_id,
        project_path=f"-Users-test-{project_name}",
        project_name=project_name,
        source_file=f"/fake/{session_id}.jsonl",
        started_at=now,
        ended_at=now + timedelta(minutes=45),
        messages=[msg],
        duration_seconds=2700,
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_cache_read_tokens=cache_read_tokens,
        total_cache_creation_tokens=cache_creation_tokens,
        estimated_cost_usd=cost,
        message_count=1,
        user_message_count=0,
        assistant_message_count=1,
        tool_call_count=2,
        file_read_count=1,
        file_write_count=0,
        file_edit_count=1,
        bash_count=0,
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

    def test_overview_contains_chart_headings(self, client):
        resp = client.get("/")
        assert b"Cost Over Time" in resp.data
        assert b"Sessions Per Week" in resp.data
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
    """Subscription user shows 'est.' prefix on cost labels."""

    def test_overview_shows_est(self, sub_client):
        resp = sub_client.get("/")
        assert b"est." in resp.data

    def test_projects_shows_est(self, sub_client):
        resp = sub_client.get("/projects")
        assert b"est." in resp.data

    def test_sessions_shows_est(self, sub_client):
        resp = sub_client.get("/sessions")
        assert b"est." in resp.data

    def test_session_detail_shows_est(self, sub_client):
        resp = sub_client.get("/sessions/sess-alpha-1")
        assert b"est." in resp.data


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
