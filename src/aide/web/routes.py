"""Route handlers — maps URLs to query functions and templates."""

from __future__ import annotations

from urllib.parse import urlencode

from flask import Blueprint, current_app, redirect, render_template, request

from aide.artifacts import accept_artifact, reject_artifact
from aide.config import save_config_value
from aide.runbook import render_project_runbook
from aide.web import queries

bp = Blueprint("dashboard", __name__)
PROVIDER_FILTERS = {"claude", "codex"}


def _provider_filter() -> str | None:
    provider = request.args.get("provider")
    if provider in PROVIDER_FILTERS:
        return provider
    return None


@bp.route("/")
def overview():
    """Overview page with summary stats, cost chart, session trends."""
    db_path = current_app.config["DB_PATH"]
    sub = current_app.config["SUBSCRIPTION_USER"]
    provider = _provider_filter()
    return render_template(
        "overview.html",
        summary=queries.get_overview_summary(db_path, provider=provider),
        data_freshness=queries.get_data_freshness(db_path),
        daily_costs=queries.get_daily_cost_series(db_path, provider=provider),
        weekly_sessions=queries.get_weekly_session_counts(db_path, provider=provider),
        weekly_work_blocks=queries.get_weekly_work_block_counts(
            db_path,
            provider=provider,
        ),
        cost_by_project=queries.get_cost_by_project(db_path, provider=provider),
        token_breakdown=queries.get_token_breakdown(db_path, provider=provider),
        effectiveness=queries.get_effectiveness_summary(db_path, provider=provider),
        effectiveness_trends=queries.get_effectiveness_trends(
            db_path,
            provider=provider,
        ),
        provider_filter=provider,
        subscription_user=sub,
    )


@bp.route("/projects")
def projects():
    """Projects page with table and scatter plot."""
    db_path = current_app.config["DB_PATH"]
    sub = current_app.config["SUBSCRIPTION_USER"]
    provider = _provider_filter()
    return render_template(
        "projects.html",
        projects=queries.get_projects_table(db_path, provider=provider),
        scatter_data=queries.get_session_scatter_data(db_path, provider=provider),
        provider_filter=provider,
        subscription_user=sub,
    )


@bp.route("/sessions")
def sessions():
    """Sessions list, optionally filtered by project."""
    db_path = current_app.config["DB_PATH"]
    sub = current_app.config["SUBSCRIPTION_USER"]
    project = request.args.get("project")
    provider = _provider_filter()
    return render_template(
        "sessions.html",
        sessions=queries.get_sessions_list(
            db_path,
            project_name=project,
            provider=provider,
        ),
        project_filter=project,
        provider_filter=provider,
        subscription_user=sub,
    )


@bp.route("/sessions/<session_id>")
def session_detail(session_id):
    """Single session detail page."""
    db_path = current_app.config["DB_PATH"]
    sub = current_app.config["SUBSCRIPTION_USER"]
    detail = queries.get_session_detail(db_path, session_id)
    if detail is None:
        return render_template("404.html", message="Session not found"), 404
    return render_template(
        "session_detail.html",
        detail=detail,
        subscription_user=sub,
    )


@bp.route("/sessions/<provider>/<session_id>")
def session_detail_for_provider(provider, session_id):
    """Single session detail page with provider-qualified identity."""
    db_path = current_app.config["DB_PATH"]
    sub = current_app.config["SUBSCRIPTION_USER"]
    detail = queries.get_session_detail(db_path, session_id, provider=provider)
    if detail is None:
        return render_template("404.html", message="Session not found"), 404
    return render_template(
        "session_detail.html",
        detail=detail,
        subscription_user=sub,
    )


@bp.route("/tools")
def tools():
    """Tool usage page with charts and top files table."""
    db_path = current_app.config["DB_PATH"]
    sub = current_app.config["SUBSCRIPTION_USER"]
    provider = _provider_filter()
    return render_template(
        "tools.html",
        tool_counts=queries.get_tool_counts(db_path, provider=provider),
        tool_weekly=queries.get_tool_weekly(db_path, provider=provider),
        tool_daily=queries.get_tool_daily(db_path, provider=provider),
        top_files=queries.get_top_files(db_path, provider=provider),
        top_commands=queries.get_top_bash_commands(db_path, provider=provider),
        error_breakdown=queries.get_error_breakdown(db_path, provider=provider),
        provider_filter=provider,
        subscription_user=sub,
    )


@bp.route("/insights")
def insights():
    """Insights page with analytical findings from session data."""
    db_path = current_app.config["DB_PATH"]
    sub = current_app.config["SUBSCRIPTION_USER"]
    provider = _provider_filter()
    return render_template(
        "insights.html",
        first_prompt=queries.get_first_prompt_analysis(db_path, provider=provider),
        cost_concentration=queries.get_cost_concentration(db_path, provider=provider),
        cost_per_edit=queries.get_cost_per_edit_by_duration(db_path, provider=provider),
        model_breakdown=queries.get_model_breakdown(db_path, provider=provider),
        tool_sequences=queries.get_tool_sequences(db_path, provider=provider),
        time_patterns=queries.get_time_patterns(db_path, provider=provider),
        response_times=queries.get_user_response_times(db_path, provider=provider),
        thinking_stats=queries.get_thinking_stats(db_path, provider=provider),
        permission_modes=queries.get_permission_mode_breakdown(
            db_path,
            provider=provider,
        ),
        permission_friction=queries.get_permission_friction_summary(
            db_path,
            provider=provider,
        ),
        investigation_queue=queries.get_investigation_queue(
            db_path,
            provider=provider,
        ),
        provider_filter=provider,
        subscription_user=sub,
    )


@bp.route("/artifacts")
def artifacts():
    """Semantic artifact review page."""
    db_path = current_app.config["DB_PATH"]
    sub = current_app.config["SUBSCRIPTION_USER"]
    project = request.args.get("project") or None
    status = request.args.get("status") or None
    artifact_type = request.args.get("type") or None
    return render_template(
        "artifacts.html",
        artifacts=queries.get_artifacts_list(
            db_path,
            project_name=project,
            status=status,
            artifact_type=artifact_type,
        ),
        filters={
            "project": project,
            "status": status,
            "type": artifact_type,
        },
        options=queries.get_artifact_filter_options(db_path),
        subscription_user=sub,
    )


@bp.route("/artifacts/<int:artifact_id>/accept", methods=["POST"])
def artifact_accept(artifact_id: int):
    """Accept a proposed artifact and return to the artifact review page."""
    db_path = current_app.config["DB_PATH"]
    note = request.form.get("note") or None
    try:
        accept_artifact(db_path, artifact_id, note=note)
    except ValueError as exc:
        return render_template("404.html", message=str(exc)), 400
    return redirect(_artifact_redirect_url())


@bp.route("/artifacts/<int:artifact_id>/reject", methods=["POST"])
def artifact_reject(artifact_id: int):
    """Reject a proposed artifact and return to the artifact review page."""
    db_path = current_app.config["DB_PATH"]
    note = request.form.get("note") or None
    try:
        reject_artifact(db_path, artifact_id, note=note)
    except ValueError as exc:
        return render_template("404.html", message=str(exc)), 400
    return redirect(_artifact_redirect_url())


@bp.route("/runbook")
def runbook():
    """Generated runbook preview for accepted artifacts."""
    db_path = current_app.config["DB_PATH"]
    sub = current_app.config["SUBSCRIPTION_USER"]
    projects = queries.get_accepted_artifact_projects(db_path)
    project_names = {item["project_name"] for item in projects}
    project = request.args.get("project") or None
    if project is None and projects:
        project = projects[0]["project_name"]
    markdown = ""
    if project:
        markdown = render_project_runbook(db_path, project)
    return render_template(
        "runbook.html",
        projects=projects,
        project_names=project_names,
        project=project,
        markdown=markdown,
        subscription_user=sub,
    )


@bp.route("/settings/subscription", methods=["POST"])
def toggle_subscription():
    """Toggle subscription_user setting and reload the page."""
    current = current_app.config["SUBSCRIPTION_USER"]
    new_value = not current
    save_config_value("subscription_user", new_value)
    current_app.config["SUBSCRIPTION_USER"] = new_value
    return redirect(request.referrer or "/")


def _artifact_redirect_url() -> str:
    params = {}
    for key in ("project", "status", "type"):
        value = request.form.get(key)
        if value:
            params[key] = value
    return "/artifacts" + (f"?{urlencode(params)}" if params else "")
