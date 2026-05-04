"""Deterministic artifact proposals from investigation action signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from aide.artifacts import list_artifacts, propose_artifact
from aide.models import ArtifactEvidence, SemanticArtifact
from aide.web.queries import (
    get_investigation_sessions_for_signal,
    get_investigation_signal_label,
)


@dataclass
class ActionProposal:
    """One proposed artifact from grouped investigation actions."""

    artifact: SemanticArtifact
    evidence: list[ArtifactEvidence] = field(default_factory=list)
    reason: str = ""


@dataclass
class ActionProposalResult:
    """Proposal set for one investigation signal."""

    signal: str
    signal_label: str
    proposals: list[ActionProposal]
    skipped_existing: int = 0


def build_action_proposals(
    db_path: Path,
    signal: str,
    *,
    provider: str | None = None,
    project_name: str | None = None,
    hours: int = 30 * 24,
) -> ActionProposalResult:
    """Build project-scoped artifact proposals for an investigation action."""
    signal_label = get_investigation_signal_label(signal) or signal
    rows = get_investigation_sessions_for_signal(
        db_path,
        signal,
        hours=hours,
        provider=provider,
    )
    if project_name is not None:
        rows = [row for row in rows if row["project_name"] == project_name]

    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        grouped.setdefault((row["provider"], row["project_name"]), []).append(row)

    proposals = []
    skipped_existing = 0
    for (_provider, _project), items in sorted(grouped.items()):
        proposal = _proposal_for_group(db_path, signal_label, items)
        if proposal is None:
            skipped_existing += 1
        else:
            proposals.append(proposal)

    return ActionProposalResult(
        signal=signal,
        signal_label=signal_label,
        proposals=proposals,
        skipped_existing=skipped_existing,
    )


def save_action_proposals(db_path: Path, result: ActionProposalResult) -> list[int]:
    """Persist action proposals as reviewable semantic artifacts."""
    artifact_ids = []
    for proposal in result.proposals:
        artifact_ids.append(
            propose_artifact(
                db_path,
                proposal.artifact,
                evidence=proposal.evidence,
                note=proposal.reason or None,
            )
        )
    return artifact_ids


def _proposal_for_group(
    db_path: Path,
    signal_label: str,
    rows: list[dict],
) -> ActionProposal | None:
    rows = sorted(rows, key=lambda row: (row["score"], row["started_at"]), reverse=True)
    first = rows[0]
    artifact_type, title, body, confidence = _artifact_text(signal_label, rows)
    if _matching_artifact_exists(db_path, first["project_name"], title):
        return None

    artifact = SemanticArtifact(
        project_name=first["project_name"],
        project_path=first.get("project_path"),
        artifact_type=artifact_type,
        title=title,
        body=body,
        confidence=confidence,
        source_provider=first["provider"],
        source_session_id=first["session_id"],
        first_seen_at=_parse_dt(min(row["started_at"] for row in rows)),
        last_seen_at=_parse_dt(max(row["started_at"] for row in rows)),
    )
    evidence = [
        ArtifactEvidence(
            artifact_id=0,
            provider=row["provider"],
            session_id=row["session_id"],
            evidence_kind="investigation_flag",
            summary=_evidence_summary(signal_label, row),
        )
        for row in rows[:3]
    ]
    return ActionProposal(
        artifact=artifact,
        evidence=evidence,
        reason=f"investigation action: {signal_label}",
    )


def _artifact_text(
    signal_label: str,
    rows: list[dict],
) -> tuple[str, str, str, str]:
    count = len(rows)
    if signal_label == "weak attribution":
        return (
            "future_agent_instruction",
            "Start future sessions from the project root",
            (
                f"{count} recent flagged session(s) had weak project attribution. "
                "Future agents should start from the repository root so search, "
                "edits, logs, and metrics attach to the intended project."
            ),
            "medium",
        )
    if signal_label == "verification command":
        return (
            "verification_recipe",
            "Persist narrow approvals for verification commands",
            (
                f"{count} recent flagged session(s) showed repeated verification "
                "command approval friction. Prefer narrow reusable prefix rules for "
                "deterministic test, lint, build, or check commands."
            ),
            "medium",
        )
    if signal_label in {"permission friction", "missing prefix rules"}:
        return (
            "risky_action",
            "Reduce repeated permission friction",
            (
                f"{count} recent flagged session(s) hit repeated permission friction. "
                "Future agents should request reusable narrow prefix rules for "
                "deterministic verification commands and keep broad mutation, "
                "system, network, and external-service commands on-request."
            ),
            "medium",
        )
    if signal_label in {"no edits", "expensive no-edit", "high cost/edit"}:
        return (
            "planner_signal",
            "Split research-only work from implementation",
            (
                f"{count} recent flagged session(s) showed low implementation leverage. "
                "When a task is likely to be exploratory, ask for or produce a short "
                "research brief before spending a full implementation session."
            ),
            "medium",
        )
    if signal_label in {"edits missing paths", "edit mismatches", "file access errors"}:
        return (
            "agent_mistake",
            "Improve file targeting before editing",
            (
                f"{count} recent flagged session(s) showed file targeting or edit "
                "attribution issues. Future agents should use repo-relative paths, "
                "reread target files before editing, and prefer structured edit tools "
                "when possible."
            ),
            "medium",
        )
    return (
        "future_agent_instruction",
        f"Review repeated {signal_label} sessions",
        (
            f"{count} recent flagged session(s) shared the `{signal_label}` signal. "
            "Review the linked sessions and update project instructions only if the "
            "pattern reflects repeatable workflow friction."
        ),
        "low",
    )


def _evidence_summary(signal_label: str, row: dict) -> str:
    parts = [
        f"{signal_label} in a flagged session",
        f"score {row['score']}",
        f"{row['tool_call_count']} tool calls",
        f"{row['tool_error_count']} tool errors",
        f"{row['edits']} edits",
    ]
    return "; ".join(parts) + "."


def _matching_artifact_exists(db_path: Path, project_name: str, title: str) -> bool:
    for status in ("proposed", "accepted"):
        for artifact in list_artifacts(db_path, project_name=project_name, status=status):
            if artifact["title"] == title:
                return True
    return False


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)
