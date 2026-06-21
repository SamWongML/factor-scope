"""On-exchange ETF scale — AUM and share count per exchange.

Reads ``{code, as_of, exchange, aum, shares}`` rows — one per ETF per disclosure, keyed by code and
stamped with the scale feed's own ``as_of`` (not the run date — it carries the spot feed's last
trading date). ``aum`` is the fund's total assets in 亿 (100M CNY), matching the scorecard's AUM
input; it is the size half of the per-fund scorecard inputs the universe carries.

Live reads AkShare's on-exchange ETF spot feed (``fund_etf_spot_em``) — one frame spanning both
exchanges, with the exchange read off the code prefix — never called in CI.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from factor_scope.store import Reading

SERIES = "etf_scale"


def _from_rows(rows: Iterable[Mapping[str, Any]], *, fetched_at: str) -> list[Reading]:
    """Map AkShare's ETF spot rows (代码 / 数据日期 / 总市值 / 最新份额 / 成交额) to Readings.

    The pure core of live: ``aum``/``shares``/``amount`` are rebased to 亿 (the unit the scorecard
    and the tier screen read), the feed's timestamp is truncated to its date, and the exchange is
    read off the code prefix (5… is Shanghai, otherwise Shenzhen). ``amount`` is the day's traded
    value (成交额) — the liquidity leg of the universe tier, free on the same once-per-run board.
    """

    return [
        Reading(
            series=SERIES,
            key=str(row["代码"]),
            as_of=str(row["数据日期"])[:10],
            fetched_at=fetched_at,
            payload={
                "exchange": "sse" if str(row["代码"]).startswith("5") else "szse",
                "aum": float(row["总市值"]) / 1e8,
                "shares": float(row["最新份额"]) / 1e8,
                "amount": float(row["成交额"]) / 1e8,
            },
        )
        for row in rows
    ]


def fetch_live(*, fetched_at: str) -> list[Reading]:  # pragma: no cover - live path
    """Pull every on-exchange ETF's AUM + shares via AkShare. Requires `live` + network."""

    import akshare as ak

    frame = ak.fund_etf_spot_em()
    return _from_rows((row for _, row in frame.iterrows()), fetched_at=fetched_at)
