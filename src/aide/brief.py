"""Generate task-specific briefs from accepted artifacts."""

from __future__ import annotations

from pathlib import Path

from aide.artifacts import get_artifact, list_artifacts

SECTION_ORDER = (
    ("future_agent_instruction", "Instructions"),
    ("verification_recipe", "Verification"),
    ("risky_action", "Risks"),
    ("agent_mistake", "Known Mistakes"),
    ("decision", "Decisions"),
    ("setup_step", "Setup"),
    ("credential_step", "Credentials"),
    ("planner_signal", "Planning Signals"),
)
MAX_ITEMS_PER_SECTION = 5


def render_project_brief(db_path: Path, project_name: str, task: str) -> str:
    """Render a concise Markdown brief for a future agent session."""
    artifacts = _accepted_artifacts_with_details(db_path, project_name)

    lines = [
        f"# {project_name} Brief",
        "",
        f"Task: {task}",
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
        section_items = by_type.get(artifact_type, [])[:MAX_ITEMS_PER_SECTION]
        if not section_items:
            continue
        lines.append(f"## {heading}")
        lines.append("")
        for item in section_items:
            source = _source_label(item)
            source_text = f" Source: {source}." if source else ""
            lines.append(f"- {item['title']}: {item['body']}{source_text}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_project_brief(
    db_path: Path,
    project_name: str,
    task: str,
    output_path: Path,
) -> None:
    """Write a generated project brief to a Markdown file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_project_brief(db_path, project_name, task))


def _accepted_artifacts_with_details(db_path: Path, project_name: str) -> list[dict]:
    rows = list_artifacts(db_path, project_name=project_name, status="accepted")
    details = []
    for row in rows:
        artifact = get_artifact(db_path, row["id"])
        if artifact is not None:
            details.append(artifact)
    details.sort(key=lambda item: (item["artifact_type"], item["id"]))
    return details


def _source_label(artifact: dict) -> str | None:
    provider = artifact.get("source_provider")
    session_id = artifact.get("source_session_id")
    if not provider or not session_id:
        return None
    return f"{provider}:{session_id}"
