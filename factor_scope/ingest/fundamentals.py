"""Per-fund valuation history — the underlying basket's PE, the valuation surface.

Reads ``{code, as_of, pe}`` rows — one per fund per disclosure, keyed by code and stamped with the
valuation feed's own trading date. ``pe`` is the tracked basket's trailing-12-month earnings
multiple; the valuation factor ranks it point-in-time against the fund's own history (a stretched
multiple is the anti-hype overvaluation gauge).

Live reads AkShare's CSI index valuation feed (``stock_zh_index_value_csindex``) for the index each
fund tracks, taking the trailing-12-month multiple (``市盈率2``); a fund with no tracked-index
mapping yields no rows and reads ``valid=False`` downstream. Never called in CI.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from factor_scope.store import Reading

SERIES = "fundamentals"

# Each on-exchange fund's tracked CSI index — the basket whose valuation history stands in for the
# fund's. A fund absent here has no valuation read and degrades to ``valid=False``.
_TRACKED_INDEX = {
    "561010": "H30202",  # 软件ETF华安 → 中证全指软件指数
    "515880": "931160",  # 通信ETF国泰 → 中证全指通信设备指数
    "588200": "000685",  # 科创芯片ETF嘉实 → 上证科创板芯片指数
}


def _from_bars(
    code: str, bars: Iterable[Mapping[str, Any]], *, fetched_at: str, since: str | None = None
) -> list[Reading]:
    """Map CSI index valuation bars (日期 / 市盈率2, the trailing-12-month multiple) to Readings.

    The pure core of live: keyed by the fund ``code`` (not the index), so the valuation factor
    ranks the fund's tracked basket against its own history. ``since`` is the incremental-fetch
    watermark — only bars strictly newer than it become rows.
    """

    return [
        Reading(
            series=SERIES,
            key=code,
            as_of=str(bar["日期"]),
            fetched_at=fetched_at,
            payload={"pe": float(bar["市盈率2"])},
        )
        for bar in bars
        if since is None or str(bar["日期"]) > since
    ]


def fetch_live(
    code: str, *, fetched_at: str, since: str | None = None
) -> list[Reading]:  # pragma: no cover - live path
    """Pull a fund basket's PE history via AkShare's CSI index valuation feed.

    Requires the `live` extra + network. A fund with no tracked-index mapping yields no rows. The
    CSI feed already serves only a short trailing window, so ``since`` (the latest stored ``as_of``)
    is applied as a write-watermark — newer bars only — with the store's content dedup as the
    backstop for the rest, rather than a network ``start_date`` the feed does not expose.
    """

    index_code = _TRACKED_INDEX.get(code)
    if index_code is None:
        return []
    import akshare as ak

    frame = ak.stock_zh_index_value_csindex(symbol=index_code)
    bars = (bar for _, bar in frame.iterrows())
    return _from_bars(code, bars, fetched_at=fetched_at, since=since)
