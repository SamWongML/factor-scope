"""The pre-materialized time-series gold tier — compact per-fund trails, served flat.

At the end of each run, the morning's per-fund readings (NAV, return, gate, factor bands) are
appended — one point per night — to a small ``<series_dir>/<code>.json`` artifact. The frontend
then charts a fund's whole trail from that static, cacheable file, with **no query in the request
path**: the read is O(nights), flat in the size of the store behind it (which grows with the whole
universe's history). This mirrors :mod:`factor_scope.history`: append-only, first-write-wins per
night, atomic writes — a later run never rewrites an earlier night's point.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import ValidationError

from factor_scope.config import Config
from factor_scope.contract import DashboardItem, FundSeries, SeriesFactor, SeriesPoint
from factor_scope.store import PointInTimeStore

# One materialized point ready to be filed under its fund: (code, name, point).
SeriesEntry = tuple[str, str, SeriesPoint]


def resolve_series_dir(config: Config) -> Path:
    """Where the per-fund trails live: the configured dir, else ``series/`` next to the artifact."""

    if config.series_dir is not None:
        return config.series_dir
    return config.output_path.parent / "series"


def materialize(
    pairs: list[tuple[str, DashboardItem]], store: PointInTimeStore, as_of: str
) -> list[SeriesEntry]:
    """Project tonight's ``(code, item)`` pairs into one compact :class:`SeriesPoint` each.

    The NAV is a single point-in-time read (the latest ``prices`` row knowable as of the run date);
    the return, gate, and valid factor bands come straight off the already-built item. A name with
    no price read carries a ``None`` NAV — it still keeps a factor trail.
    """

    navs = {r.key: float(r.payload["nav"]) for r in store.read_as_of("prices", as_of)}
    return [
        (
            code,
            item.item,
            SeriesPoint(
                as_of=as_of,
                nav=navs.get(code),
                gain=item.gain,
                gate=item.gate,
                factors=[
                    SeriesFactor(factor=s.factor, level=s.level) for s in item.states if s.valid
                ],
            ),
        )
        for code, item in pairs
    ]


def _write_atomic(path: Path, text: str) -> None:
    """Stage in the same directory + rename, so a concurrent reader never sees a partial file."""

    staging = path.with_name(path.name + ".staging")
    staging.write_text(text, encoding="utf-8")
    os.replace(staging, path)


def record(entries: list[SeriesEntry], series_dir: Path) -> None:
    """Append each fund's point to its trail, immutably per night, oldest first.

    The first point recorded for an ``as_of`` stands: re-running a night leaves its existing point
    untouched, so a later run never rewrites an earlier night (mirroring the append-only store).
    """

    if not entries:
        return
    series_dir.mkdir(parents=True, exist_ok=True)
    for code, name, point in entries:
        existing = load(series_dir, code)
        points = list(existing.points) if existing is not None else []
        if any(p.as_of == point.as_of for p in points):
            continue
        points.append(point)
        points.sort(key=lambda p: p.as_of)
        series = FundSeries(code=code, name=name, points=points)
        _write_atomic(series_dir / f"{code}.json", series.model_dump_json(indent=2))


def load(series_dir: Path, code: str) -> FundSeries | None:
    """The recorded trail for one fund, or ``None`` when that fund has no materialized series."""

    path = series_dir / f"{code}.json"
    if not path.is_file():
        return None
    try:
        return FundSeries.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, OSError):
        return None


def list_codes(series_dir: Path) -> list[str]:
    """Every fund with a materialized trail, sorted — the catalog ``/series`` lists."""

    if not series_dir.is_dir():
        return []
    return sorted(p.stem for p in series_dir.glob("*.json"))
