"""Persistence helpers for reviewable semantic artifacts.

This module is intentionally small and heuristic-free. It stores proposed
artifacts, summarized evidence, and lifecycle events so later digest/review
features can build on a stable core API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from aide.db import get_connection
from aide.models import (
    ARTIFACT_CONFIDENCES,
    ARTIFACT_EVENT_TYPES,
    ARTIFACT_EVIDENCE_KINDS,
    ARTIFACT_STATUSES,
    ARTIFACT_TYPES,
    ArtifactEvidence,
    SemanticArtifact,
)


def propose_artifact(
    db_path: Path,
    artifact: SemanticArtifact,
    evidence: list[ArtifactEvidence] | None = None,
    note: str | None = None,
) -> int:
    """Store a proposed artifact and return its database id."""
    _validate_artifact(artifact)
    if artifact.status != "proposed":
        raise ValueError("new artifacts must start with status 'proposed'")

    evidence_items = evidence or []
    for item in evidence_items:
        _validate_evidence(item)

    con = get_connection(db_path)
    try:
        now = _now()
        row = con.execute(
            """INSERT INTO semantic_artifacts (
                project_name, project_path, artifact_type, title, body,
                status, confidence, source_provider, source_session_id,
                source_message_uuid, first_seen_at, last_seen_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                artifact.project_name,
                artifact.project_path,
                artifact.artifact_type,
                artifact.title,
                artifact.body,
                artifact.status,
                artifact.confidence,
                artifact.source_provider,
                artifact.source_session_id,
                artifact.source_message_uuid,
                artifact.first_seen_at.isoformat(),
                artifact.last_seen_at.isoformat(),
                now,
                now,
            ),
        )
        artifact_id = int(row.lastrowid)
        for item in evidence_items:
            _insert_evidence(con, artifact_id, item)
        _insert_event(con, artifact_id, "proposed", note=note)
        con.commit()
        return artifact_id
    finally:
        con.close()


def add_artifact_evidence(db_path: Path, evidence: ArtifactEvidence) -> int:
    """Append summarized evidence to an existing artifact."""
    _validate_evidence(evidence)
    con = get_connection(db_path)
    try:
        _require_artifact(con, evidence.artifact_id)
        evidence_id = _insert_evidence(con, evidence.artifact_id, evidence)
        con.commit()
        return evidence_id
    finally:
        con.close()


def get_artifact(db_path: Path, artifact_id: int) -> dict | None:
    """Fetch one artifact with evidence and lifecycle events."""
    con = get_connection(db_path)
    try:
        row = con.execute(
            "SELECT * FROM semantic_artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        if row is None:
            return None
        artifact = dict(row)
        artifact["evidence"] = [
            dict(item)
            for item in con.execute(
                """SELECT * FROM artifact_evidence
                WHERE artifact_id = ?
                ORDER BY id""",
                (artifact_id,),
            ).fetchall()
        ]
        artifact["events"] = [
            dict(item)
            for item in con.execute(
                """SELECT * FROM artifact_events
                WHERE artifact_id = ?
                ORDER BY id""",
                (artifact_id,),
            ).fetchall()
        ]
        return artifact
    finally:
        con.close()


def list_artifacts(
    db_path: Path,
    project_name: str | None = None,
    status: str | None = None,
    artifact_type: str | None = None,
) -> list[dict]:
    """List artifacts, optionally filtered by project, status, and type."""
    if status is not None:
        _validate_choice("status", status, ARTIFACT_STATUSES)
    if artifact_type is not None:
        _validate_choice("artifact_type", artifact_type, ARTIFACT_TYPES)

    clauses = []
    params = []
    if project_name is not None:
        clauses.append("project_name = ?")
        params.append(project_name)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if artifact_type is not None:
        clauses.append("artifact_type = ?")
        params.append(artifact_type)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    con = get_connection(db_path)
    try:
        rows = con.execute(
            f"""SELECT * FROM semantic_artifacts
            {where}
            ORDER BY updated_at DESC, id DESC""",
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def accept_artifact(db_path: Path, artifact_id: int, note: str | None = None) -> dict:
    """Mark a proposed artifact as accepted and append a lifecycle event."""
    return _transition_artifact(db_path, artifact_id, "accepted", note=note)


def reject_artifact(db_path: Path, artifact_id: int, note: str | None = None) -> dict:
    """Mark a proposed artifact as rejected and append a lifecycle event."""
    return _transition_artifact(db_path, artifact_id, "rejected", note=note)


def _transition_artifact(
    db_path: Path,
    artifact_id: int,
    status: str,
    note: str | None = None,
) -> dict:
    _validate_choice("status", status, ARTIFACT_STATUSES)
    if status not in {"accepted", "rejected"}:
        raise ValueError("only accepted/rejected transitions are supported")

    con = get_connection(db_path)
    try:
        artifact = _require_artifact(con, artifact_id)
        if artifact["status"] != "proposed":
            raise ValueError("only proposed artifacts can be accepted or rejected")

        now = _now()
        accepted_at = now if status == "accepted" else None
        rejected_at = now if status == "rejected" else None
        con.execute(
            """UPDATE semantic_artifacts
            SET status = ?,
                accepted_at = COALESCE(?, accepted_at),
                rejected_at = COALESCE(?, rejected_at),
                updated_at = ?
            WHERE id = ?""",
            (status, accepted_at, rejected_at, now, artifact_id),
        )
        _insert_event(con, artifact_id, status, note=note)
        con.commit()
        updated = con.execute(
            "SELECT * FROM semantic_artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        return dict(updated)
    finally:
        con.close()


def _insert_evidence(con, artifact_id: int, evidence: ArtifactEvidence) -> int:
    row = con.execute(
        """INSERT INTO artifact_evidence (
            artifact_id, provider, session_id, message_uuid, tool_name,
            evidence_kind, summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            artifact_id,
            evidence.provider,
            evidence.session_id,
            evidence.message_uuid,
            evidence.tool_name,
            evidence.evidence_kind,
            evidence.summary,
        ),
    )
    return int(row.lastrowid)


def _insert_event(
    con,
    artifact_id: int,
    event_type: str,
    note: str | None = None,
) -> int:
    _validate_choice("event_type", event_type, ARTIFACT_EVENT_TYPES)
    row = con.execute(
        "INSERT INTO artifact_events (artifact_id, event_type, note) VALUES (?, ?, ?)",
        (artifact_id, event_type, note),
    )
    return int(row.lastrowid)


def _require_artifact(con, artifact_id: int) -> dict:
    row = con.execute(
        "SELECT * FROM semantic_artifacts WHERE id = ?",
        (artifact_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Artifact not found: {artifact_id}")
    return dict(row)


def _validate_artifact(artifact: SemanticArtifact) -> None:
    _validate_choice("artifact_type", artifact.artifact_type, ARTIFACT_TYPES)
    _validate_choice("status", artifact.status, ARTIFACT_STATUSES)
    _validate_choice("confidence", artifact.confidence, ARTIFACT_CONFIDENCES)
    _validate_required("project_name", artifact.project_name)
    _validate_required("title", artifact.title)
    _validate_required("body", artifact.body)


def _validate_evidence(evidence: ArtifactEvidence) -> None:
    _validate_choice("evidence_kind", evidence.evidence_kind, ARTIFACT_EVIDENCE_KINDS)
    _validate_required("provider", evidence.provider)
    _validate_required("session_id", evidence.session_id)
    _validate_required("summary", evidence.summary)


def _validate_choice(name: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(f"Unsupported {name}: {value}. Expected one of: {allowed_values}")


def _validate_required(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} is required")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
