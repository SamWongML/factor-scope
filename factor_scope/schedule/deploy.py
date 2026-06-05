"""Render the nightly job's scheduler config — launchd (macOS) or cron (Linux).

A :class:`ScheduleSpec` captures *what* to run, *when*, *where*, and *where its output goes*. Both
renderers are pure functions of that spec — no platform calls, no wall-clock — so the scheduled job
is deterministic and reviewable before it is installed. The launchd plist is the documented
Mac-mini production path (D4); cron is the Linux alternative.
"""

from __future__ import annotations

import plistlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The engine stamps ``generated_at`` at 22:00 (spec §02); the nightly job fires after the close, so
# 22:00 local is the sensible default. These are defaults only — every field is overridable.
DEFAULT_LABEL = "com.factor-scope.nightly"
DEFAULT_PROGRAM = ("factor-scope", "nightly", "--fixtures")


@dataclass(frozen=True)
class ScheduleSpec:
    """Everything a scheduler needs to fire the nightly job once a day (spec §11, D4)."""

    label: str = DEFAULT_LABEL
    program_arguments: tuple[str, ...] = DEFAULT_PROGRAM
    hour: int = 22  # local hour to fire (24h)
    minute: int = 0
    working_directory: Path = field(default_factory=Path.cwd)
    stdout_path: Path = Path("out/nightly.out.log")
    stderr_path: Path = Path("out/nightly.err.log")
    environment: dict[str, str] | None = None  # extra env (e.g. FRED_API_KEY) for --live runs


def render_cron_line(spec: ScheduleSpec) -> str:
    """A single crontab line (5-field schedule) that runs the job, appending its streams to logs."""

    command = " ".join(spec.program_arguments)
    return (
        f"{spec.minute} {spec.hour} * * * "
        f"cd {spec.working_directory} && {command} "
        f">> {spec.stdout_path} 2>> {spec.stderr_path}"
    )


def render_launchd_plist(spec: ScheduleSpec) -> str:
    """A macOS launchd property list firing the job daily at ``hour:minute`` (one-shot, no daemon).

    Built via :mod:`plistlib` so the output is guaranteed-valid plist XML. ``RunAtLoad`` is false —
    this is a scheduled nightly batch, not a service that should start when the agent is loaded.
    """

    payload: dict[str, Any] = {
        "Label": spec.label,
        "ProgramArguments": list(spec.program_arguments),
        "StartCalendarInterval": {"Hour": spec.hour, "Minute": spec.minute},
        "WorkingDirectory": str(spec.working_directory),
        "StandardOutPath": str(spec.stdout_path),
        "StandardErrorPath": str(spec.stderr_path),
        "RunAtLoad": False,
    }
    if spec.environment:
        payload["EnvironmentVariables"] = dict(spec.environment)
    return plistlib.dumps(payload, sort_keys=False).decode("utf-8")
