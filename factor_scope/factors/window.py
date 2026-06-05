"""Point-in-time series helpers for the factor battery.

Every factor reads from the append-only store and must stay point-in-time: only rows whose
``as_of`` is on or before the run date are visible, ordered oldest-first. These helpers do that
projection once so the factor functions stay pure and small. No wall clock, no network.
"""

from __future__ import annotations

import statistics

from factor_scope.store import PointInTimeStore, Reading


def _series_asc(store: PointInTimeStore, series: str, key: str, as_of: str) -> list[Reading]:
    rows = [r for r in store.history(series, key) if r.as_of <= as_of]
    rows.sort(key=lambda r: (r.as_of, r.fetched_at))
    return rows


def price_navs(store: PointInTimeStore, code: str, as_of: str) -> list[float]:
    """The point-in-time NAV history for ``code``, oldest-first."""

    return [float(r.payload["nav"]) for r in _series_asc(store, "prices", code, as_of)]


def fred_values(store: PointInTimeStore, series_id: str, as_of: str) -> list[float]:
    """The point-in-time value history for a FRED series, oldest-first."""

    return [float(r.payload["value"]) for r in _series_asc(store, "fred", series_id, as_of)]


def fred_latest(store: PointInTimeStore, series_id: str, as_of: str) -> float | None:
    """The most recent value for a FRED series as of the run date, or ``None`` if absent."""

    values = fred_values(store, series_id, as_of)
    return values[-1] if values else None


def horizon_returns(navs: list[float], lookback: int) -> list[float]:
    """The series of ``lookback``-period simple returns over ``navs``."""

    return [navs[i] / navs[i - lookback] - 1.0 for i in range(lookback, len(navs))]


def rolling_vol(navs: list[float], window: int) -> list[float]:
    """The series of rolling realised volatilities (population std of daily returns)."""

    rets = [navs[i] / navs[i - 1] - 1.0 for i in range(1, len(navs))]
    return [statistics.pstdev(rets[i - window : i]) for i in range(window, len(rets) + 1)]


def drawdown(navs: list[float]) -> float:
    """Current drawdown: latest NAV vs the running peak (≤ 0)."""

    peak = max(navs)
    return navs[-1] / peak - 1.0


def moving_average(navs: list[float], window: int) -> float:
    """The simple moving average of the last ``window`` points."""

    return sum(navs[-window:]) / window


__all__ = [
    "drawdown",
    "fred_latest",
    "fred_values",
    "horizon_returns",
    "moving_average",
    "price_navs",
    "rolling_vol",
]
