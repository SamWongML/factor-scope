"""Scheduling adapter + nightly ops.

This package makes the nightly run *operable* without platform code on the critical path.
Two thin, deterministic pieces live here:

- :mod:`~factor_scope.schedule.deploy` — render a macOS **launchd** plist (the Mac-mini production
  path) or a **cron** line (the Linux alternative) from a :class:`ScheduleSpec`. Pure string
  renders, so the scheduled job is reviewable before it is ever installed.
- :mod:`~factor_scope.schedule.runlog` — a structured, append-only :class:`RunRecord` ops log
  (start/end, item counts, abstains, provider, cost note) written beside ``dashboard.json``.

The orchestration itself (ingest → compute → digest → write artifact → log run → persist calls)
is :func:`factor_scope.pipeline.nightly`.
"""

from __future__ import annotations

from factor_scope.schedule.deploy import (
    ScheduleSpec,
    render_cron_line,
    render_launchd_plist,
)
from factor_scope.schedule.runlog import (
    DigestFailure,
    RunRecord,
    append_run_log,
    cost_note,
    summarize_run,
)

__all__ = [
    "DigestFailure",
    "RunRecord",
    "ScheduleSpec",
    "append_run_log",
    "cost_note",
    "render_cron_line",
    "render_launchd_plist",
    "summarize_run",
]
