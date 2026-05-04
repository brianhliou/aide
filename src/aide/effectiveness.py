"""Persisted effectiveness snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from aide.db import get_connection, init_db
from aide.web.queries import (
    get_effectiveness_overview,
    get_effectiveness_project_rollups,
)

ALL_VALUE = "__all__"


@dataclass(frozen=True)
class EffectivenessSnapshotRow:
    """One effectiveness snapshot row."""

    snapshot_date: str
    window_days: int
    scope: str
    provider: str
    project_name: str
    metrics: dict


def snapshot_effectiveness(
    db_path: Path,
    *,
    snapshot_date: date | None = None,
    window_days: int = 30,
) -> list[EffectivenessSnapshotRow]:
    """Persist all/provider/project effectiveness metrics for one date."""
    init_db(db_path)
    day = (snapshot_date or date.today()).isoformat()
    rows = build_effectiveness_snapshot_rows(
        db_path,
        snapshot_date=day,
        window_days=window_days,
    )
    save_effectiveness_snapshot_rows(db_path, rows)
    return rows


def build_effectiveness_snapshot_rows(
    db_path: Path,
    *,
    snapshot_date: str,
    window_days: int = 30,
) -> list[EffectivenessSnapshotRow]:
    """Build snapshot rows without writing them."""
    rows = [
        EffectivenessSnapshotRow(
            snapshot_date=snapshot_date,
            window_days=window_days,
            scope="all",
            provider=ALL_VALUE,
            project_name=ALL_VALUE,
            metrics=get_effectiveness_overview(db_path, days=window_days)["current"],
        )
    ]

    for provider in _providers_with_sessions(db_path):
        metrics = get_effectiveness_overview(
            db_path,
            days=window_days,
            provider=provider,
        )["current"]
        if metrics["session_count"] > 0:
            rows.append(
                EffectivenessSnapshotRow(
                    snapshot_date=snapshot_date,
                    window_days=window_days,
                    scope="provider",
                    provider=provider,
                    project_name=ALL_VALUE,
                    metrics=metrics,
                )
            )

    for item in get_effectiveness_project_rollups(db_path, days=window_days):
        rows.append(
            EffectivenessSnapshotRow(
                snapshot_date=snapshot_date,
                window_days=window_days,
                scope="project",
                provider=item["provider"],
                project_name=item["project_name"],
                metrics=item["current"],
            )
        )

    return rows


def save_effectiveness_snapshot_rows(
    db_path: Path,
    rows: list[EffectivenessSnapshotRow],
) -> None:
    """Upsert effectiveness snapshot rows."""
    con = get_connection(db_path)
    try:
        for row in rows:
            metrics = row.metrics
            con.execute(
                """INSERT INTO effectiveness_snapshots (
                    snapshot_date, window_days, scope, provider, project_name,
                    session_count, total_cost, avg_cost_per_session,
                    avg_active_seconds, tool_call_count, tool_error_count,
                    error_rate, no_edit_session_count, no_edit_rate,
                    edit_call_count, attributed_edit_call_count,
                    edit_attribution_rate, cost_per_edit, review_session_count,
                    review_rate, review_score, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    datetime('now'))
                ON CONFLICT(snapshot_date, window_days, scope, provider, project_name)
                DO UPDATE SET
                    session_count = excluded.session_count,
                    total_cost = excluded.total_cost,
                    avg_cost_per_session = excluded.avg_cost_per_session,
                    avg_active_seconds = excluded.avg_active_seconds,
                    tool_call_count = excluded.tool_call_count,
                    tool_error_count = excluded.tool_error_count,
                    error_rate = excluded.error_rate,
                    no_edit_session_count = excluded.no_edit_session_count,
                    no_edit_rate = excluded.no_edit_rate,
                    edit_call_count = excluded.edit_call_count,
                    attributed_edit_call_count = excluded.attributed_edit_call_count,
                    edit_attribution_rate = excluded.edit_attribution_rate,
                    cost_per_edit = excluded.cost_per_edit,
                    review_session_count = excluded.review_session_count,
                    review_rate = excluded.review_rate,
                    review_score = excluded.review_score,
                    updated_at = datetime('now')""",
                (
                    row.snapshot_date,
                    row.window_days,
                    row.scope,
                    row.provider,
                    row.project_name,
                    metrics["session_count"],
                    metrics["total_cost"],
                    metrics["avg_cost_per_session"],
                    metrics["avg_active_seconds"],
                    metrics["tool_call_count"],
                    metrics["tool_error_count"],
                    metrics["error_rate"],
                    metrics["no_edit_session_count"],
                    metrics["no_edit_rate"],
                    metrics["edit_call_count"],
                    metrics["attributed_edit_call_count"],
                    metrics["edit_attribution_rate"],
                    metrics["cost_per_edit"],
                    metrics["review_session_count"],
                    metrics["review_rate"],
                    metrics["review_score"],
                ),
            )
        con.commit()
    finally:
        con.close()


def list_effectiveness_snapshots(
    db_path: Path,
    *,
    limit: int = 20,
) -> list[dict]:
    """Return recent snapshot rows for CLI inspection."""
    con = get_connection(db_path)
    try:
        rows = con.execute(
            """SELECT * FROM effectiveness_snapshots
            ORDER BY snapshot_date DESC, scope, provider, project_name
            LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def _providers_with_sessions(db_path: Path) -> list[str]:
    con = get_connection(db_path)
    try:
        rows = con.execute(
            "SELECT DISTINCT provider FROM sessions ORDER BY provider"
        ).fetchall()
        return [row["provider"] for row in rows]
    finally:
        con.close()
