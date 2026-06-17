"""Quarterly fund/ETF holdings. Reads ``{fund, as_of, holding, weight}`` rows.

These rows become the connection-graph edges (the exact look-through). Each ``(fund,
holding)`` pair is its own point-in-time key so a quarter's disclosure never overwrites a prior one.
Live is AkShare's ``fund_portfolio_hold_em`` — never called in CI.
"""

from __future__ import annotations

from factor_scope.store import Reading

SERIES = "fund_holdings"


def fetch_live(
    fund: str, *, fetched_at: str, since: str | None = None
) -> list[Reading]:  # pragma: no cover - live path
    """Pull a fund's disclosed stock holdings via AkShare. Requires `live` + network.

    AkShare's ``fund_portfolio_hold_em`` is queried per calendar year. ``since`` is the latest
    quarter already stored for this fund: the request spans only that disclosure year onward (the
    run year when nothing is stored — derived from the run stamp, never a hard-coded lookback), and
    any quarter at or before the watermark is dropped, so a re-pull adds only newly disclosed ones.
    """

    import akshare as ak

    readings: list[Reading] = []
    for year in range(_first_year(since, fetched_at), int(fetched_at[:4]) + 1):
        frame = ak.fund_portfolio_hold_em(symbol=fund, date=str(year))
        for _, row in frame.iterrows():
            as_of = str(row["季度"])
            if since is not None and as_of <= since:
                continue
            readings.append(
                Reading(
                    series=SERIES,
                    key=f"{fund}/{row['股票名称']}",
                    as_of=as_of,
                    fetched_at=fetched_at,
                    payload={
                        "fund": fund,
                        "holding": str(row["股票名称"]),
                        "weight": float(row["占净值比例"]) / 100.0,
                    },
                )
            )
    return readings


def _first_year(since: str | None, fetched_at: str) -> int:
    """The earliest disclosure year to request: the watermark's year, else the run stamp's year."""

    return int((since or fetched_at)[:4])
