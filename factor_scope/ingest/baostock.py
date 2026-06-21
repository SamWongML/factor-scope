"""Prices adapter (CN, Baostock) — a second, independent source for the ``prices`` series.

CN ingestion is dual/triple-sourced (AkShare + Baostock + Mootdx) so one scraper being
IP-blocked or offline never kills a nightly run. This module is the Baostock leg: a thin
``fetch_live`` that reads the latest daily close for one ETF, stamped into the same ``prices``
:class:`~factor_scope.store.Reading` shape as the AkShare adapter. It therefore either corroborates
the AkShare read (cross-validation) or substitutes for it when AkShare is down — the selection
policy lives in :func:`factor_scope.ingest.prices.select_corroborated`.

Mootdx/pytdx is the planned third source, added as a separate adapter leg.

Like every live backend, the heavy dependency is imported lazily inside the call so the core
installs and CI run offline; ``fetch_live`` is the default live path and never called in CI (which
forces offline).
"""

from __future__ import annotations

from datetime import timedelta

from factor_scope.ingest.base import day_after, run_date
from factor_scope.ingest.prices import _SEED_CALENDAR_DAYS, SERIES
from factor_scope.store import Reading

SOURCE = "baostock"  # this adapter's provenance tag
_ADJUST_NONE = "3"  # Baostock adjustflag: 1=后复权 2=前复权 3=不复权 — raw close, matching AkShare


def _market_code(code: str) -> str:
    """Prefix a bare ETF code with its Baostock market — ``5x`` is Shanghai, ``1x`` is Shenzhen."""

    return f"sh.{code}" if code.startswith("5") else f"sz.{code}"


def _start_date(fetched_at: str, since: str | None) -> str | None:
    """Baostock's ``start_date`` (``YYYY-MM-DD``): watermark+1 incrementally, else the seed floor.

    ``None`` when there is no watermark and the run stamp is not a real date (the unit fakes), so
    the seed degrades to Baostock's default full range rather than raising.
    """

    if since is not None:
        return day_after(since).isoformat()
    anchor = run_date(fetched_at)
    return (anchor - timedelta(days=_SEED_CALENDAR_DAYS)).isoformat() if anchor else None


def fetch_live(  # pragma: no cover - live path
    code: str, *, fetched_at: str, since: str | None = None
) -> list[Reading]:
    """Pull one ETF's daily-close history via Baostock. Requires the `live` extra + network.

    Returns the same windowed/incremental contract as the AkShare leg: a ~400-trading-day seed when
    cold (``since`` is None), only sessions past the watermark thereafter, so the price series is
    corroborated across the full window, not just the latest bar.
    """

    import baostock as bs

    start = _start_date(fetched_at, since)
    query_kwargs = {"start_date": start} if start else {}
    bs.login()
    try:
        result = bs.query_history_k_data_plus(
            _market_code(code), "date,close", frequency="d", adjustflag=_ADJUST_NONE, **query_kwargs
        )
        rows: list[list[str]] = []
        while result.next():
            rows.append(result.get_row_data())
    finally:
        bs.logout()

    return [
        Reading(
            series=SERIES,
            key=code,
            as_of=str(as_of),
            fetched_at=fetched_at,
            payload={"nav": float(close), "source": SOURCE},
        )
        for as_of, close in rows
        if since is None or str(as_of) > since
    ]
