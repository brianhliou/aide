"""Tests for launchd job status helpers."""

from __future__ import annotations

import plistlib
from types import SimpleNamespace

from aide.jobs import (
    LaunchdJobDefinition,
    collect_launchd_job_status,
    format_launchd_schedule,
    parse_launchctl_print,
)

LAUNCHCTL_PRINT = """
gui/501/com.brianliou.aide.ingest = {
    path = /Users/test/Library/LaunchAgents/com.brianliou.aide.ingest.plist
    state = not running
    stdout path = /Users/test/.local/share/aide/ingest.log
    stderr path = /Users/test/.local/share/aide/ingest.err
    runs = 5
    last exit code = 0
    event triggers = {
        descriptor = {
            "Minute" => 0
            "Hour" => 12
        }
    }
}
"""


def test_parse_launchctl_print_extracts_status_fields():
    parsed = parse_launchctl_print(LAUNCHCTL_PRINT)

    assert parsed["plist_path"].endswith("com.brianliou.aide.ingest.plist")
    assert parsed["state"] == "not running"
    assert parsed["runs"] == 5
    assert parsed["last_exit_code"] == 0
    assert parsed["stdout_path"].endswith("ingest.log")
    assert parsed["stderr_path"].endswith("ingest.err")
    assert parsed["schedule"] == "daily 12:00"


def test_format_launchd_schedule_from_plist_dict():
    assert format_launchd_schedule({"StartCalendarInterval": {"Hour": 12, "Minute": 5}}) == (
        "daily 12:05"
    )


def test_collect_launchd_job_status_loaded_with_logs(tmp_path):
    plist = tmp_path / "job.plist"
    stdout = tmp_path / "job.log"
    stderr = tmp_path / "job.err"
    stdout.write_text("old\n\nlatest line\n")
    stderr.write_text("")
    plist.write_bytes(
        plistlib.dumps(
            {
                "Label": "com.example.job",
                "StandardOutPath": str(stdout),
                "StandardErrorPath": str(stderr),
                "StartCalendarInterval": {"Hour": 9, "Minute": 30},
            }
        )
    )

    def runner(args, capture_output, text, check):
        assert args == ["launchctl", "print", "gui/501/com.example.job"]
        return SimpleNamespace(
            returncode=0,
            stdout=LAUNCHCTL_PRINT.replace(
                "/Users/test/Library/LaunchAgents/com.brianliou.aide.ingest.plist",
                str(plist),
            )
            .replace("/Users/test/.local/share/aide/ingest.log", str(stdout))
            .replace("/Users/test/.local/share/aide/ingest.err", str(stderr)),
            stderr="",
        )

    status = collect_launchd_job_status(
        LaunchdJobDefinition("example", "com.example.job", plist),
        uid=501,
        runner=runner,
    )

    assert status.loaded is True
    assert status.healthy is True
    assert status.plist_exists is True
    assert status.state == "not running"
    assert status.runs == 5
    assert status.last_exit_code == 0
    assert status.schedule == "daily 12:00"
    assert status.stdout_last_lines == ["old", "latest line"]
    assert status.stderr_size == 0


def test_collect_launchd_job_status_missing_job_uses_plist_metadata(tmp_path):
    plist = tmp_path / "job.plist"
    stdout = tmp_path / "job.log"
    plist.write_bytes(
        plistlib.dumps(
            {
                "Label": "com.example.job",
                "StandardOutPath": str(stdout),
                "StartCalendarInterval": {"Hour": 7, "Minute": 15},
            }
        )
    )

    def runner(args, capture_output, text, check):
        return SimpleNamespace(
            returncode=113,
            stdout="",
            stderr="Could not find service",
        )

    status = collect_launchd_job_status(
        LaunchdJobDefinition("example", "com.example.job", plist),
        uid=501,
        runner=runner,
    )

    assert status.loaded is False
    assert status.healthy is False
    assert status.error == "Could not find service"
    assert status.schedule == "daily 07:15"
    assert status.stdout_path == stdout


def test_collect_launchd_job_status_failed_exit_code(tmp_path):
    plist = tmp_path / "job.plist"
    plist.write_bytes(plistlib.dumps({"Label": "com.example.job"}))

    def runner(args, capture_output, text, check):
        return SimpleNamespace(
            returncode=0,
            stdout=LAUNCHCTL_PRINT.replace("last exit code = 0", "last exit code = 2"),
            stderr="",
        )

    status = collect_launchd_job_status(
        LaunchdJobDefinition("example", "com.example.job", plist),
        uid=501,
        runner=runner,
    )

    assert status.loaded is True
    assert status.last_exit_code == 2
    assert status.healthy is False
