"""Prices adapter (CN, Mootdx) — the third, independent source for the ``prices`` series.

CN ingestion is dual/triple-sourced (AkShare + Baostock + Mootdx) so one scraper being
IP-blocked or offline never kills a nightly run. This module is the Mootdx leg: a thin
``fetch_live`` that reads the latest daily close for one ETF over the TDX (通达信) market protocol,
stamped into the same ``prices`` :class:`~factor_scope.store.Reading` shape as the other two legs.
With three sources, :func:`factor_scope.ingest.prices.select_reconciled` takes the **median** — so a
single bad source (including the AkShare primary) can no longer poison the NAV.

Like every live backend, the heavy dependency is imported lazily inside the call so the core
installs and CI run offline; ``fetch_live`` is opt-in (behind ``--live``) and never called in CI.
"""

from __future__ import annotations

from factor_scope.ingest.prices import SERIES
from factor_scope.store import Reading

SOURCE = "mootdx"  # this adapter's provenance tag
_DAILY = 9  # Mootdx frequency code for the daily K-line; bars are unadjusted (raw close)


def fetch_live(code: str, *, fetched_at: str) -> list[Reading]:  # pragma: no cover - opt-in
    """Pull the latest daily close for one ETF via Mootdx. Requires the `live` extra + network."""

    from mootdx.quotes import Quotes

    client = Quotes.factory(market="std")
    frame = client.bars(symbol=code, frequency=_DAILY, offset=1)  # the latest daily bar
    if frame is None or frame.empty:  # unknown/delisted code → no data, so the caller falls back
        return []
    last = frame.iloc[-1]
    return [
        Reading(
            series=SERIES,
            key=code,
            as_of=str(frame.index[-1])[:10],
            fetched_at=fetched_at,
            payload={"nav": float(last["close"]), "source": SOURCE},
        )
    ]
