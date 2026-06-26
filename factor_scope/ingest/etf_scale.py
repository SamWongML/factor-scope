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

from factor_scope.ingest.base import spot_date
from factor_scope.store import Reading

SERIES = "etf_scale"


def _board_float(value: Any) -> float:
    """``float`` that degrades a missing (None) cell to NaN rather than raising at the board's edge.

    AkShare usually yields NaN for a missing numeric cell (and ``float(nan)`` is fine), but an
    occasional None would raise — and this row is the *shared* board's edge, so one bad cell would
    otherwise kill the whole board build and every fund's universe/scale/current-bar leg, not just
    that row. A missing value degrades to NaN — the same absent state a NaN cell already yields —
    kept like any other invalid reading, never dropped or raised (CLAUDE.md: inputs degrade).
    """

    return float("nan") if value is None else float(value)


def _board_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise one raw AkShare spot row (Chinese columns) to the shared domain board row.

    The single source-vocabulary boundary: ``fund_etf_spot_em``'s columns (代码 / 数据日期 /
    最新价 / 换手率 / 成交额 / 总市值 / 最新份额) are mapped once here to the domain keys every
    downstream leg reads (scale, the price + activity current-bar fallbacks, and the settled-session
    check), so no Chinese key leaks past the board's edge. Values stay in the source's raw units
    (元/份); each consumer rebases (etf_scale → 亿; activity keeps the raw input).
    """

    return {
        "code": str(row["代码"]),
        "date": spot_date(row["数据日期"]),
        "nav": _board_float(row["最新价"]),
        "turnover": _board_float(row["换手率"]),
        "amount": _board_float(row["成交额"]),
        "aum": _board_float(row["总市值"]),
        "shares": _board_float(row["最新份额"]),
    }


def _from_rows(rows: Iterable[Mapping[str, Any]], *, fetched_at: str) -> list[Reading]:
    """Map the normalised domain board rows to ETF-scale Readings.

    ``aum``/``shares``/``amount`` are rebased to 亿 (the unit the scorecard and the tier screen
    read) and the exchange is read off the code prefix (5… is Shanghai, otherwise Shenzhen).
    ``amount`` is the day's traded value — the liquidity leg of the universe tier, free on the same
    once-per-run board.
    """

    return [
        Reading(
            series=SERIES,
            key=row["code"],
            as_of=row["date"],
            fetched_at=fetched_at,
            payload={
                "exchange": "sse" if row["code"].startswith("5") else "szse",
                "aum": row["aum"] / 1e8,
                "shares": row["shares"] / 1e8,
                "amount": row["amount"] / 1e8,
            },
        )
        for row in rows
    ]


def fetch_spot_board() -> dict[str, Any]:  # pragma: no cover - live path
    """The whole-market on-exchange ETF spot board, indexed by fund code — one batch call per run.

    This single snapshot is the shared input the live feed hands to the universe-membership,
    ETF-scale, and per-fund current-bar legs, so ``fund_etf_spot_em`` is pulled once per run rather
    than once per leg. Each raw row is normalised at this edge (:func:`_board_row`) so every
    consumer reads domain keys; indexed by code so the per-fund lookup is O(1).
    """

    import akshare as ak

    board: dict[str, Any] = {}
    for _, raw in ak.fund_etf_spot_em().iterrows():
        row = _board_row(raw)
        board[row["code"]] = row
    return board


def fetch_live(board: Mapping[str, Any], *, fetched_at: str) -> list[Reading]:
    """Map every on-exchange ETF's row on the shared spot board to an AUM/shares/amount Reading."""

    return _from_rows(board.values(), fetched_at=fetched_at)
