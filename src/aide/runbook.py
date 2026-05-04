"""Generate deterministic Markdown runbooks from accepted artifacts."""

from __future__ import annotations

from pathlib import Path

from aide.artifacts import get_artifact, list_artifacts

SECTION_ORDER = (
    ("decision", "Decisions"),
    ("setup_step", "Setup Steps"),
    ("credential_step", "Credential Steps"),
    ("verification_recipe", "Verification Recipes"),
    ("agent_mistake", "Known Mistakes"),
    ("risky_action", "Risky Actions"),
    ("future_agent_instruction", "Future Agent Instructions"),
    ("planner_signal", "Planning Signals"),
)


def render_project_runbook(db_path: Path, project_name: str) -> str:
    """Render a project runbook from accepted artifacts only."""
    artifacts = _accepted_artifacts_with_details(db_path, project_name)

    lines = [
        f"# {project_name} Runbook",
        "",
        "> Generated from accepted aide semantic artifacts.",
        "",
    ]

    if not artifacts:
        lines.extend([
            "No accepted artifacts found for this project.",
            "",
        ])
        return "\n".join(lines)

    by_type: dict[str, list[dict]] = {}
    for artifact in artifacts:
        by_type.setdefault(artifact["artifact_type"], []).append(artifact)

    for artifact_type, heading in SECTION_ORDER:
        section_items = by_type.get(artifact_type, [])
        if not section_items:
            continue
        lines.append(f"## {heading}")
        lines.append("")
        for item in section_items:
            lines.extend(_render_artifact(item))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_project_runbook(db_path: Path, project_name: str, output_path: Path) -> None:
    """Write a generated project runbook to a Markdown file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_project_runbook(db_path, project_name))


def _accepted_artifacts_with_details(db_path: Path, project_name: str) -> list[dict]:
    rows = list_artifacts(db_path, project_name=project_name, status="accepted")
    details = []
    for row in rows:
        artifact = get_artifact(db_path, row["id"])
        if artifact is not None:
            details.append(artifact)
    details.sort(key=lambda item: (item["artifact_type"], item["id"]))
    return details


def _render_artifact(artifact: dict) -> list[str]:
    lines = [
        f"### {artifact['title']}",
        "",
        artifact["body"],
        "",
    ]

    source = _source_label(artifact)
    metadata = [
        f"Artifact: #{artifact['id']}",
        f"Confidence: {artifact['confidence']}",
    ]
    if source:
        metadata.append(f"Source: {source}")
    lines.append("_" + " | ".join(metadata) + "_")

    if artifact["evidence"]:
        lines.append("")
        lines.append("Evidence:")
        for item in artifact["evidence"]:
            tool = f" via {item['tool_name']}" if item["tool_name"] else ""
            lines.append(f"- {item['evidence_kind']}{tool}: {item['summary']}")

    lines.append("")
    return lines


def _source_label(artifact: dict) -> str | None:
    provider = artifact.get("source_provider")
    session_id = artifact.get("source_session_id")
    if not provider or not session_id:
        return None
    return f"{provider}:{session_id}"
