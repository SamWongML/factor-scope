"""The dashboard history — one immutable artifact per night, plus its index manifest.

``run`` records every artifact it emits as ``<history_dir>/<as_of>.json`` and regenerates
``index.json`` beside it, so any past morning can be reopened, inspected, or served. A later run
never rewrites an earlier night; re-running the same ``as_of`` over the same frozen snapshot
rewrites that night byte-for-byte (the artifact path stays clock-free). The index derives only
from the files on disk — rebuilt by scanning, so it self-heals and stays deterministic.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import ValidationError

from factor_scope.config import Config
from factor_scope.contract import Dashboard, DashboardIndex, DashboardIndexEntry

INDEX_NAME = "index.json"


def resolve_history_dir(config: Config) -> Path:
    """Where the per-night artifacts live: the configured dir, else next to the artifact."""

    if config.history_dir is not None:
        return config.history_dir
    return config.output_path.parent / "dashboards"


def _write_atomic(path: Path, text: str) -> None:
    """Stage in the same directory + rename, so a concurrent reader never sees a partial file."""

    staging = path.with_name(path.name + ".staging")
    staging.write_text(text, encoding="utf-8")
    os.replace(staging, path)


def record(dash: Dashboard, history_dir: Path) -> Path:
    """Persist one night into the history and refresh the index. Returns the dated path."""

    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / f"{dash.as_of}.json"
    _write_atomic(path, dash.model_dump_json(indent=2))
    _write_atomic(history_dir / INDEX_NAME, read_index(history_dir).model_dump_json(indent=2))
    return path


def read_index(history_dir: Path) -> DashboardIndex:
    """The manifest of recorded nights, oldest first — derived purely from the files on disk.

    A file that does not parse as a :class:`Dashboard` degrades to absence (skipped), never
    an error — the rest of the history stays listable.
    """

    entries = []
    if history_dir.is_dir():
        for path in sorted(history_dir.glob("*.json")):
            if path.name == INDEX_NAME:
                continue
            dash = _read_dashboard(path)
            if dash is None:
                continue
            entries.append(
                DashboardIndexEntry(
                    as_of=dash.as_of,
                    generated_at=dash.generated_at,
                    snapshot_id=dash.snapshot_id,
                    n_items=len(dash.items),
                )
            )
    entries.sort(key=lambda e: e.as_of)
    return DashboardIndex(entries=entries)


def load(history_dir: Path, as_of: str) -> Dashboard | None:
    """The recorded artifact for one night, or None when that night isn't in the history."""

    return _read_dashboard(history_dir / f"{as_of}.json")


def _read_dashboard(path: Path) -> Dashboard | None:
    if not path.is_file():
        return None
    try:
        return Dashboard.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, OSError):
        return None
