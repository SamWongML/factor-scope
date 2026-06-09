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


def turnovers(store: PointInTimeStore, code: str, as_of: str) -> list[float]:
    """The point-in-time daily turnover (换手率) history for ``code``, oldest-first."""

    rows = _series_asc(store, "trading_activity", code, as_of)
    return [float(r.payload["turnover"]) for r in rows]


def traded_values(store: PointInTimeStore, code: str, as_of: str) -> list[float]:
    """The point-in-time daily traded value (成交额, the Amihud input), oldest-first."""

    rows = _series_asc(store, "trading_activity", code, as_of)
    return [float(r.payload["amount"]) for r in rows]


def valuation_pes(store: PointInTimeStore, code: str, as_of: str) -> list[float]:
    """The point-in-time PE (市盈率) history for a fund's basket, oldest-first."""

    return [float(r.payload["pe"]) for r in _series_asc(store, "fundamentals", code, as_of)]


def demand_revisions(store: PointInTimeStore, as_of: str) -> list[float]:
    """The book-wide end-demand revision history (one series, all keys), oldest-first."""

    rows = [r for r in store.history("demand") if r.as_of <= as_of]
    rows.sort(key=lambda r: (r.as_of, r.fetched_at))
    return [float(r.payload["revision"]) for r in rows]


def lead_chain(store: PointInTimeStore, as_of: str) -> list[float]:
    """The US lead-chain: total 13F-disclosed shares of the leaders per as_of, oldest-first.

    Aggregates every point-in-time ``edgar`` 13F row (the ``shares`` disclosures, not the weighted
    N-PORT graph edges) into one book-wide accumulation series the cross-market factor ranks.
    """

    by_date: dict[str, float] = {}
    for r in store.history("edgar"):
        if "shares" in r.payload and r.as_of <= as_of:
            by_date[r.as_of] = by_date.get(r.as_of, 0.0) + float(r.payload["shares"])
    return [by_date[d] for d in sorted(by_date)]


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
    "demand_revisions",
    "drawdown",
    "fred_latest",
    "fred_values",
    "horizon_returns",
    "lead_chain",
    "moving_average",
    "price_navs",
    "rolling_vol",
    "traded_values",
    "turnovers",
    "valuation_pes",
]
