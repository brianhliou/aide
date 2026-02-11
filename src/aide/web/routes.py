"""Route handlers — maps URLs to query functions and templates."""

from __future__ import annotations

from flask import Blueprint, current_app, render_template, request

from aide.web import queries

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def overview():
    """Overview page with summary stats, cost chart, session trends."""
    db_path = current_app.config["DB_PATH"]
    sub = current_app.config["SUBSCRIPTION_USER"]
    return render_template(
        "overview.html",
        summary=queries.get_overview_summary(db_path),
        daily_costs=queries.get_daily_cost_series(db_path),
        weekly_sessions=queries.get_weekly_session_counts(db_path),
        cost_by_project=queries.get_cost_by_project(db_path),
        token_breakdown=queries.get_token_breakdown(db_path),
        subscription_user=sub,
    )


@bp.route("/projects")
def projects():
    """Projects page with table and scatter plot."""
    db_path = current_app.config["DB_PATH"]
    sub = current_app.config["SUBSCRIPTION_USER"]
    return render_template(
        "projects.html",
        projects=queries.get_projects_table(db_path),
        scatter_data=queries.get_session_scatter_data(db_path),
        subscription_user=sub,
    )


@bp.route("/sessions")
def sessions():
    """Sessions list, optionally filtered by project."""
    db_path = current_app.config["DB_PATH"]
    sub = current_app.config["SUBSCRIPTION_USER"]
    project = request.args.get("project")
    return render_template(
        "sessions.html",
        sessions=queries.get_sessions_list(db_path, project_name=project),
        project_filter=project,
        subscription_user=sub,
    )


@bp.route("/sessions/<session_id>")
def session_detail(session_id):
    """Single session detail page."""
    db_path = current_app.config["DB_PATH"]
    sub = current_app.config["SUBSCRIPTION_USER"]
    detail = queries.get_session_detail(db_path, session_id)
    if detail is None:
        return "Session not found", 404
    return render_template(
        "session_detail.html",
        detail=detail,
        subscription_user=sub,
    )


@bp.route("/tools")
def tools():
    """Tool usage page with charts and top files table."""
    db_path = current_app.config["DB_PATH"]
    return render_template(
        "tools.html",
        tool_counts=queries.get_tool_counts(db_path),
        tool_weekly=queries.get_tool_weekly(db_path),
        top_files=queries.get_top_files(db_path),
    )
