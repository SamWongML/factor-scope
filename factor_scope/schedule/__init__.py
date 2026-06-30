"""Scheduling adapter + nightly ops.

This package makes the nightly run *operable* without platform code on the critical path.
Two thin, deterministic pieces live here:

- :mod:`~factor_scope.schedule.deploy` — render a macOS **launchd** plist (the Mac-mini production
  path) or a **cron** line (the Linux alternative) from a :class:`ScheduleSpec`. Pure string
  renders, so the scheduled job is reviewable before it is ever installed.
- :mod:`~factor_scope.schedule.runlog` — a structured, append-only :class:`RunRecord` ops log
  (start/end, item counts, abstains, provider, per-provider cost + budget) by ``dashboard.json``.

The orchestration itself (ingest → compute → digest → write artifact → log run → persist calls)
is :func:`factor_scope.pipeline.nightly`.
"""

from __future__ import annotations

from factor_scope.schedule.deploy import (
    DEFAULT_PROGRAM,
    DISCOVER_PROGRAM,
    ScheduleSpec,
    render_cron_line,
    render_launchd_plist,
    resume_spec,
)
from factor_scope.schedule.runlog import (
    DigestFailure,
    RunRecord,
    append_run_log,
    summarize_run,
)

__all__ = [
    "DEFAULT_PROGRAM",
    "DISCOVER_PROGRAM",
    "DigestFailure",
    "RunRecord",
    "ScheduleSpec",
    "append_run_log",
    "render_cron_line",
    "render_launchd_plist",
    "resume_spec",
    "summarize_run",
]
