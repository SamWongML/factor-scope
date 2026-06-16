"""The dashboard history — one immutable artifact per night, plus a materialized catalog.

``run`` records every artifact it emits as ``<history_dir>/<as_of>.json``, so any past morning
can be reopened, inspected, or served. The first recording of an ``as_of`` stands: a later run
never rewrites an earlier night, mirroring the append-only store (a later disclosure never
rewrites an earlier read).

Alongside the night files lives ``index.json`` — a small catalog the frontend lists nights from
in O(1), holding just what each entry needs (``as_of`` / ``generated_at`` / ``snapshot_id`` /
``n_items``), all known at record time. The catalog is a cache, never the source of truth: it is
appended to only when a night is recorded, and :func:`read_index` falls back to deriving the index
straight from the night files whenever the catalog is missing or unreadable.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import ValidationError

from factor_scope.config import Config
from factor_scope.contract import Dashboard, DashboardIndex, DashboardIndexEntry

# The materialized catalog, sitting beside the dated night files it indexes.
_INDEX_NAME = "index.json"


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


def _entry(dash: Dashboard) -> DashboardIndexEntry:
    return DashboardIndexEntry(
        as_of=dash.as_of,
        generated_at=dash.generated_at,
        snapshot_id=dash.snapshot_id,
        n_items=len(dash.items),
    )


def record(dash: Dashboard, history_dir: Path) -> Path:
    """Persist one night into the history, immutably, and catalog it. Returns the dated path.

    The first recording of an ``as_of`` stands: if the night is already on disk it is left
    untouched and the catalog is not retouched, so a later run never rewrites an earlier night.
    """

    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / f"{dash.as_of}.json"
    if path.exists():
        return path
    _write_atomic(path, dash.model_dump_json(indent=2))
    _catalog_night(history_dir, _entry(dash))
    return path


def _catalog_night(history_dir: Path, entry: DashboardIndexEntry) -> None:
    """Append one night to the catalog, oldest first; first entry for an ``as_of`` wins.

    Starts from the catalog when it is sound, else from a disk rebuild — so a lost or corrupt
    catalog self-heals on the next recording rather than dropping earlier nights.
    """

    index = _read_catalog(history_dir) or _rebuild_index(history_dir)
    by_as_of = {e.as_of: e for e in index.entries}
    by_as_of.setdefault(entry.as_of, entry)
    entries = sorted(by_as_of.values(), key=lambda e: e.as_of)
    _write_atomic(history_dir / _INDEX_NAME, DashboardIndex(entries=entries).model_dump_json())


def read_index(history_dir: Path) -> DashboardIndex:
    """The catalog of recorded nights, oldest first — O(1) from the materialized ``index.json``.

    A missing or unreadable catalog degrades to a rebuild straight from the night files (each
    file that does not parse as a :class:`Dashboard` is skipped), never an error.
    """

    return _read_catalog(history_dir) or _rebuild_index(history_dir)


def _read_catalog(history_dir: Path) -> DashboardIndex | None:
    """The materialized catalog, or None when it is absent or does not parse."""

    path = history_dir / _INDEX_NAME
    if not path.is_file():
        return None
    try:
        return DashboardIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, OSError):
        return None


def _rebuild_index(history_dir: Path) -> DashboardIndex:
    """The catalog reconstructed purely from the night files on disk — the O(N) fallback."""

    entries = []
    if history_dir.is_dir():
        for path in sorted(history_dir.glob("*.json")):
            if path.name == _INDEX_NAME:
                continue
            dash = _read_dashboard(path)
            if dash is None:
                continue
            entries.append(_entry(dash))
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
