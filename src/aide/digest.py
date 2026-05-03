"""Heuristic session digest proposals.

Digest is the bridge from normalized analytics to reviewable artifacts. This
module does not call LLMs and does not persist anything unless the caller asks
it to save generated proposals.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from aide.artifacts import propose_artifact
from aide.autopsy.queries import get_session, get_session_tool_calls
from aide.models import ArtifactEvidence, SemanticArtifact

_VERIFICATION_COMMAND_PREFIXES = (
    "just check",
    "just test",
    "make check",
    "make test",
    "uv run pytest",
    "uv run ruff",
    "pytest",
    "ruff check",
    "npm run build",
    "npm run test",
    "npm run lint",
    "npm run typecheck",
    "yarn build",
    "yarn test",
    "pnpm build",
    "pnpm test",
)
_WEAK_PROJECT_NAMES = frozenset({"codex", "projects", "sessions", "unknown"})


@dataclass
class DigestProposal:
    """One proposed artifact with summarized evidence."""

    artifact: SemanticArtifact
    evidence: list[ArtifactEvidence] = field(default_factory=list)
    reason: str = ""


@dataclass
class DigestResult:
    """Artifact proposals for one normalized session."""

    session: dict
    proposals: list[DigestProposal]


def build_digest(
    db_path: Path,
    session_id: str,
    provider: str | None = None,
) -> DigestResult | None:
    """Build heuristic artifact proposals for one session."""
    session = get_session(db_path, session_id, provider=provider)
    if session is None:
        return None

    actual_provider = session["provider"]
    tool_calls = get_session_tool_calls(
        db_path,
        session["session_id"],
        provider=actual_provider,
    )
    proposals = _build_proposals(session, tool_calls)
    return DigestResult(session=session, proposals=proposals)


def save_digest_proposals(db_path: Path, result: DigestResult) -> list[int]:
    """Persist digest proposals as proposed artifacts."""
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


def _build_proposals(session: dict, tool_calls: list[dict]) -> list[DigestProposal]:
    proposals = []
    proposals.extend(_verification_recipe_proposals(session, tool_calls))
    risky = _permission_risk_proposal(session, tool_calls)
    if risky is not None:
        proposals.append(risky)
    mistake = _agent_mistake_proposal(session, tool_calls)
    if mistake is not None:
        proposals.append(mistake)
    planner = _planner_signal_proposal(session)
    if planner is not None:
        proposals.append(planner)
    weak_project = _weak_project_proposal(session)
    if weak_project is not None:
        proposals.append(weak_project)
    return proposals


def _verification_recipe_proposals(
    session: dict,
    tool_calls: list[dict],
) -> list[DigestProposal]:
    commands = []
    seen = set()
    for tc in tool_calls:
        if tc["tool_name"] != "Bash" or tc["is_error"]:
            continue
        command = _verification_command(tc["command"])
        if command and command not in seen:
            commands.append((command, tc))
            seen.add(command)

    proposals = []
    for command, tc in commands[:3]:
        proposals.append(
            DigestProposal(
                artifact=_artifact(
                    session,
                    artifact_type="verification_recipe",
                    title=f"Verify with `{command}`",
                    body=f"Use `{command}` as a known verification command for this project.",
                    confidence="high",
                    source_message_uuid=tc["message_uuid"],
                ),
                evidence=[
                    _evidence(
                        session,
                        evidence_kind="verification_result",
                        summary="A recognized verification command completed successfully.",
                        message_uuid=tc["message_uuid"],
                        tool_name=tc["tool_name"],
                    )
                ],
                reason="successful verification command",
            )
        )
    return proposals


def _permission_risk_proposal(
    session: dict,
    tool_calls: list[dict],
) -> DigestProposal | None:
    friction = [
        tc
        for tc in tool_calls
        if _contains_permission_signal(tc["command"], tc["description"])
    ]
    if len(friction) < 2:
        return None

    return DigestProposal(
        artifact=_artifact(
            session,
            artifact_type="risky_action",
            title="Review repeated permission escalations",
            body=(
                "This session needed repeated permission or sandbox escalation. "
                "Future agents should prefer narrow, reusable approvals for "
                "deterministic verification commands and keep system or external "
                "service actions on-request."
            ),
            confidence="medium",
            source_message_uuid=friction[0]["message_uuid"],
        ),
        evidence=[
            _evidence(
                session,
                evidence_kind="permission_friction",
                summary=f"{len(friction)} permission-related tool events were observed.",
                message_uuid=friction[0]["message_uuid"],
                tool_name=friction[0]["tool_name"],
            )
        ],
        reason="permission friction",
    )


def _agent_mistake_proposal(
    session: dict,
    tool_calls: list[dict],
) -> DigestProposal | None:
    edit_mismatches = [
        tc for tc in tool_calls if tc["tool_name"] == "Edit" and tc["is_error"]
    ]
    file_access_errors = [
        tc
        for tc in tool_calls
        if tc["tool_name"] in {"Read", "Write", "Glob", "Grep"} and tc["is_error"]
    ]
    pathless_edits = [
        tc
        for tc in tool_calls
        if tc["tool_name"] in {"Edit", "Write"} and tc["file_path"] is None
    ]
    if len(edit_mismatches) < 2 and len(file_access_errors) < 3 and len(pathless_edits) < 2:
        return None

    parts = []
    if edit_mismatches:
        parts.append(f"{len(edit_mismatches)} edit mismatches")
    if file_access_errors:
        parts.append(f"{len(file_access_errors)} file-access failures")
    if pathless_edits:
        parts.append(f"{len(pathless_edits)} edit/write calls without file paths")

    first = (edit_mismatches or file_access_errors or pathless_edits)[0]
    return DigestProposal(
        artifact=_artifact(
            session,
            artifact_type="agent_mistake",
            title="Improve file targeting before editing",
            body=(
                "This session showed shaky file targeting: "
                + ", ".join(parts)
                + ". Future agents should re-read the target file or verify the "
                "exact path before applying edits."
            ),
            confidence="medium",
            source_message_uuid=first["message_uuid"],
        ),
        evidence=[
            _evidence(
                session,
                evidence_kind="error_pattern",
                summary=", ".join(parts),
                message_uuid=first["message_uuid"],
                tool_name=first["tool_name"],
            )
        ],
        reason="file targeting errors",
    )


def _planner_signal_proposal(session: dict) -> DigestProposal | None:
    edits = (session["file_edit_count"] or 0) + (session["file_write_count"] or 0)
    tool_calls = session["tool_call_count"] or 0
    cost = session["estimated_cost_usd"] or 0.0
    active_seconds = session["active_duration_seconds"] or 0
    duration_seconds = session["duration_seconds"] or 0

    expensive_no_edit = edits == 0 and tool_calls >= 10 and cost >= 1.0
    low_active = (
        tool_calls >= 10
        and 0 < active_seconds <= 60
        and active_seconds / max(duration_seconds, 1) <= 0.05
    )
    if not expensive_no_edit and not low_active:
        return None

    signals = []
    if expensive_no_edit:
        signals.append("high-cost/no-edit work")
    if low_active:
        signals.append("suspiciously low active time")

    return DigestProposal(
        artifact=_artifact(
            session,
            artifact_type="planner_signal",
            title="Review session leverage",
            body=(
                "This session may deserve planning review because it showed "
                + " and ".join(signals)
                + "."
            ),
            confidence="medium",
        ),
        evidence=[
            _evidence(
                session,
                evidence_kind="investigation_flag",
                summary=(
                    f"{tool_calls} tool calls, {edits} edits/writes, "
                    f"${cost:.2f} estimated cost, {active_seconds}s active time."
                ),
            )
        ],
        reason="investigation queue signal",
    )


def _weak_project_proposal(session: dict) -> DigestProposal | None:
    project_name = (session["project_name"] or "").lower()
    project_path = session["project_path"] or ""
    if project_name not in _WEAK_PROJECT_NAMES and not project_path.endswith("/.codex"):
        return None

    return DigestProposal(
        artifact=_artifact(
            session,
            artifact_type="future_agent_instruction",
            title="Start future sessions from the project root",
            body=(
                "Project attribution looked weak for this session. Future agents "
                "should start from the repository root so logs and metrics attach "
                "to the intended project."
            ),
            confidence="low",
        ),
        evidence=[
            _evidence(
                session,
                evidence_kind="investigation_flag",
                summary=(
                    f"Session project was attributed as "
                    f"`{session['project_name']}` from `{session['project_path']}`."
                ),
            )
        ],
        reason="weak project attribution",
    )


def _artifact(
    session: dict,
    artifact_type: str,
    title: str,
    body: str,
    confidence: str,
    source_message_uuid: str | None = None,
) -> SemanticArtifact:
    first_seen = _parse_dt(session["started_at"])
    last_seen = _parse_dt(session["ended_at"] or session["started_at"])
    return SemanticArtifact(
        project_name=session["project_name"],
        project_path=session["project_path"],
        artifact_type=artifact_type,
        title=title,
        body=body,
        confidence=confidence,
        source_provider=session["provider"],
        source_session_id=session["session_id"],
        source_message_uuid=source_message_uuid,
        first_seen_at=first_seen,
        last_seen_at=last_seen,
    )


def _evidence(
    session: dict,
    evidence_kind: str,
    summary: str,
    message_uuid: str | None = None,
    tool_name: str | None = None,
) -> ArtifactEvidence:
    return ArtifactEvidence(
        artifact_id=0,
        provider=session["provider"],
        session_id=session["session_id"],
        message_uuid=message_uuid,
        tool_name=tool_name,
        evidence_kind=evidence_kind,
        summary=summary,
    )


def _verification_command(command: str | None) -> str | None:
    if not command:
        return None
    normalized = _command_family(command)
    for prefix in _VERIFICATION_COMMAND_PREFIXES:
        if normalized == prefix or normalized.startswith(f"{prefix} "):
            return normalized
    return None


def _command_family(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts:
        return ""
    if parts[0] == "env":
        parts = _strip_env(parts[1:])
    else:
        parts = _strip_env(parts)
    if not parts:
        return ""
    if parts[0] == "uv" and len(parts) >= 3 and parts[1] == "run":
        return " ".join(parts[:3])
    if parts[0] in {"npm", "yarn", "pnpm"}:
        script = _script_name(parts)
        return f"{parts[0]} run {script}" if script else " ".join(parts[:2])
    if parts[0] in {"just", "make"} and len(parts) >= 2:
        return " ".join(parts[:2])
    if parts[0] == "ruff" and len(parts) >= 2:
        return " ".join(parts[:2])
    return parts[0]


def _strip_env(parts: list[str]) -> list[str]:
    index = 0
    while index < len(parts) and _looks_env(parts[index]):
        index += 1
    return parts[index:]


def _looks_env(value: str) -> bool:
    if "=" not in value or value.startswith("="):
        return False
    key = value.split("=", 1)[0]
    return key.replace("_", "").isalnum()


def _script_name(parts: list[str]) -> str | None:
    if "run" in parts:
        index = parts.index("run")
        if index + 1 < len(parts):
            return parts[index + 1]
    return None


def _contains_permission_signal(command: str | None, description: str | None) -> bool:
    text = " ".join(part for part in (command, description) if part).lower()
    return any(
        marker in text
        for marker in (
            "permission",
            "sandbox",
            "require_escalated",
            "sandbox_permissions",
            "prefix_rule",
            "operation not permitted",
        )
    )


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)
