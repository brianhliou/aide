"""Local launchd job status helpers for aide background tasks."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LaunchdJobDefinition:
    """A launchd job aide knows how to report."""

    name: str
    label: str
    plist_path: Path


@dataclass
class LaunchdJobStatus:
    """Parsed launchd and filesystem status for one job."""

    name: str
    label: str
    plist_path: Path
    plist_exists: bool
    loaded: bool
    state: str | None = None
    runs: int | None = None
    last_exit_code: int | None = None
    schedule: str | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    stdout_updated_at: datetime | None = None
    stderr_updated_at: datetime | None = None
    stdout_last_lines: list[str] | None = None
    stderr_size: int | None = None
    error: str | None = None

    @property
    def healthy(self) -> bool:
        if not self.plist_exists or not self.loaded:
            return False
        return self.last_exit_code in (0, None)


def default_launchd_jobs(home: Path | None = None) -> list[LaunchdJobDefinition]:
    """Return aide's expected per-user launchd jobs."""
    root = home or Path.home()
    launch_agents = root / "Library" / "LaunchAgents"
    return [
        LaunchdJobDefinition(
            name="ingest",
            label="com.brianliou.aide.ingest",
            plist_path=launch_agents / "com.brianliou.aide.ingest.plist",
        ),
        LaunchdJobDefinition(
            name="redacted backup",
            label="com.brianliou.aide.backup-redacted",
            plist_path=launch_agents / "com.brianliou.aide.backup-redacted.plist",
        ),
    ]


def collect_launchd_job_statuses(
    jobs: list[LaunchdJobDefinition] | None = None,
    uid: int | None = None,
    runner: Any = subprocess.run,
) -> list[LaunchdJobStatus]:
    """Collect launchd, plist, and log metadata for known aide jobs."""
    return [
        collect_launchd_job_status(job, uid=uid, runner=runner)
        for job in (jobs or default_launchd_jobs())
    ]


def collect_launchd_job_status(
    job: LaunchdJobDefinition,
    uid: int | None = None,
    runner: Any = subprocess.run,
) -> LaunchdJobStatus:
    """Collect status for one launchd job."""
    plist = read_launchd_plist(job.plist_path)
    parsed: dict[str, Any] = {}
    loaded = False
    error = None

    domain = f"gui/{uid if uid is not None else os.getuid()}"
    try:
        result = runner(
            ["launchctl", "print", f"{domain}/{job.label}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        error = str(exc)
    else:
        if result.returncode == 0:
            loaded = True
            parsed = parse_launchctl_print(result.stdout)
        else:
            error = _first_nonempty_line(result.stderr) or _first_nonempty_line(
                result.stdout
            )

    stdout_path = _path_or_none(
        parsed.get("stdout_path") or plist.get("StandardOutPath")
    )
    stderr_path = _path_or_none(
        parsed.get("stderr_path") or plist.get("StandardErrorPath")
    )

    status = LaunchdJobStatus(
        name=job.name,
        label=job.label,
        plist_path=Path(parsed.get("plist_path") or job.plist_path),
        plist_exists=job.plist_path.exists(),
        loaded=loaded,
        state=parsed.get("state"),
        runs=parsed.get("runs"),
        last_exit_code=parsed.get("last_exit_code"),
        schedule=parsed.get("schedule") or format_launchd_schedule(plist),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        stdout_updated_at=_file_mtime(stdout_path),
        stderr_updated_at=_file_mtime(stderr_path),
        stdout_last_lines=_tail_nonempty_lines(stdout_path, limit=3),
        stderr_size=_file_size(stderr_path),
        error=error,
    )
    return status


def parse_launchctl_print(output: str) -> dict[str, Any]:
    """Parse selected fields from `launchctl print` output."""
    minute = _extract_int(output, r'"Minute"\s*=>\s*(\d+)')
    hour = _extract_int(output, r'"Hour"\s*=>\s*(\d+)')
    return {
        "plist_path": _extract_text(output, r"^\s*path = (.+)$"),
        "state": _extract_text(output, r"^\s*state = (.+)$"),
        "runs": _extract_int(output, r"^\s*runs = (\d+)$"),
        "last_exit_code": _extract_int(output, r"^\s*last exit code = (-?\d+)$"),
        "stdout_path": _extract_text(output, r"^\s*stdout path = (.+)$"),
        "stderr_path": _extract_text(output, r"^\s*stderr path = (.+)$"),
        "schedule": _format_time(hour, minute),
    }


def read_launchd_plist(path: Path) -> dict[str, Any]:
    """Read a launchd plist if present."""
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        data = plistlib.load(f)
    return data if isinstance(data, dict) else {}


def format_launchd_schedule(plist: dict[str, Any]) -> str | None:
    """Format StartCalendarInterval from a launchd plist."""
    interval = plist.get("StartCalendarInterval")
    if isinstance(interval, dict):
        return _format_time(interval.get("Hour"), interval.get("Minute"))
    if isinstance(interval, list):
        parts = [
            _format_time(item.get("Hour"), item.get("Minute"))
            for item in interval
            if isinstance(item, dict)
        ]
        return ", ".join(part for part in parts if part) or None
    return None


def format_timestamp(value: datetime | None) -> str:
    """Format local file timestamps for CLI output."""
    if value is None:
        return "missing"
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _extract_text(output: str, pattern: str) -> str | None:
    match = re.search(pattern, output, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _extract_int(output: str, pattern: str) -> int | None:
    text = _extract_text(output, pattern)
    return int(text) if text is not None else None


def _format_time(hour: Any, minute: Any) -> str | None:
    if hour is None and minute is None:
        return None
    hour_value = int(hour or 0)
    minute_value = int(minute or 0)
    return f"daily {hour_value:02d}:{minute_value:02d}"


def _path_or_none(value: Any) -> Path | None:
    return Path(value) if value else None


def _file_mtime(path: Path | None) -> datetime | None:
    if path is None or not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone()


def _file_size(path: Path | None) -> int | None:
    if path is None or not path.exists():
        return None
    return path.stat().st_size


def _tail_nonempty_lines(path: Path | None, limit: int) -> list[str]:
    if path is None or not path.exists():
        return []
    lines = [line.strip() for line in path.read_text(errors="replace").splitlines()]
    return [line for line in lines if line][-limit:]


def _first_nonempty_line(value: str | None) -> str | None:
    if not value:
        return None
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None
