"""Regenerate the price + FRED history fixtures the factor states rank against.

The factor battery ranks each reading against its *own* history, so the fixtures need a real
distribution to rank into (a single latest NAV per code is not enough). This generator
emits deterministic, dependency-free synthetic series — no RNG, no wall clock — so a fixtures run
stays byte-for-byte reproducible. Run from the repo root:

    uv run python scripts/gen_price_fixtures.py

It rewrites ``data/fixtures/prices.csv`` and ``data/fixtures/fred.csv``. The shapes are chosen so
the artifact tells a clear story: three holdings/watchlist codes ride above their 200-day MA
(gate ``open``), while the emerging energy-storage ETF sits in a drawdown below it (gate ``capped``),
and the macro dial reads "tight" (a high real-yield percentile vs its own two-year history).
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"
N_DAYS = 220  # > 200 so the 200-day trend gate is computable for every code


def _weekdays_ending(end: date, n: int) -> list[date]:
    """The ``n`` most recent weekdays up to and including ``end`` (oldest first)."""

    days: list[date] = []
    cur = end
    while len(days) < n:
        if cur.weekday() < 5:  # Mon-Fri
            days.append(cur)
        cur -= timedelta(days=1)
    return list(reversed(days))


def _wiggle(i: int, amp: float) -> float:
    """A deterministic, seedless oscillation — texture without a wall-clock RNG."""

    return amp * math.sin(i * 0.7) + 0.5 * amp * math.sin(i * 0.23)


def _ramp(
    *, start: float, end: float, n: int, amp: float, late_kick: float = 0.0
) -> list[float]:
    """A smooth start→end path with a deterministic wiggle and an optional last-20-day kick."""

    out: list[float] = []
    for i in range(n):
        t = i / (n - 1)
        base = start + (end - start) * t
        val = base * (1.0 + _wiggle(i, amp))
        if late_kick and i >= n - 20:
            # A fresh run-up over the last month → a "ran-up-hard" reversal read.
            val += late_kick * base * ((i - (n - 20)) / 20.0)
        out.append(val)
    return out


def _rescale_last(vals: list[float], target: float) -> list[float]:
    """Shift the path so its final point lands exactly on ``target`` (keeps gains consistent)."""

    delta = target - vals[-1]
    return [v + delta for v in vals]


# (code, end_date, final_nav, start_nav, amp, late_kick) — one trajectory per book code.
_PRICE_SPECS = [
    # Optical-module ETF: steady climb + a sharp last-month run-up → reversal-DOWN risk, gate open.
    ("561010", date(2026, 6, 5), 1.921, 1.42, 0.012, 0.10),
    # Comms ETF: gentle uptrend, mild gain, gate open.
    ("515880", date(2026, 6, 5), 1.178, 0.96, 0.010, 0.0),
    # Chip ETF (watchlist): uptrend, gate open.
    ("588200", date(2026, 6, 5), 1.103, 0.84, 0.014, 0.0),
    # Energy-storage ETF (emerging): peaked then bled lower → drawdown below 200-MA, gate CAPPED.
    ("561160", date(2026, 6, 4), 0.982, 1.46, 0.011, 0.0),
]


def gen_prices() -> str:
    lines = ["code,as_of,nav"]
    for code, end, final, start, amp, kick in _PRICE_SPECS:
        days = _weekdays_ending(end, N_DAYS)
        vals = _rescale_last(
            _ramp(start=start, end=final, n=N_DAYS, amp=amp, late_kick=kick), final
        )
        for d, v in zip(days, vals, strict=True):
            lines.append(f"{code},{d.isoformat()},{round(v, 3)}")
    return "\n".join(lines) + "\n"


def gen_fred() -> str:
    """The macro dial: a two-year monthly real-yield (DFII10) history with the dollar / CNY now.

    DFII10 rises from ~0.6% to 1.88% so the current reading sits in the top percentile of its own
    history → a "tight" (liquidity-headwind) book-wide regime. The other series stay single current
    rows: they are evidence on the dial, not ranked.
    """

    lines = ["series_id,as_of,value"]
    # 24 monthly DFII10 observations ending 2026-06-04 at 1.88, trending up from 0.62.
    months: list[date] = []
    y, m = 2024, 7
    for _ in range(23):
        months.append(date(y, m, 1))
        m += 1
        if m > 12:
            m = 1
            y += 1
    months.append(date(2026, 6, 4))  # the current reading (matches the live snapshot date)
    for i, d in enumerate(months):
        t = i / (len(months) - 1)
        val = 0.62 + (1.88 - 0.62) * t + _wiggle(i, 0.02)
        val = 1.88 if i == len(months) - 1 else round(val, 2)
        lines.append(f"DFII10,{d.isoformat()},{val}")
    # Supporting current readings (carried as evidence on the dial, not ranked).
    lines.append("DGS10,2026-06-04,4.21")
    lines.append("T10YIE,2026-06-04,2.33")
    lines.append("DTWEXBGS,2026-06-03,121.4")
    lines.append("DEXCHUS,2026-06-03,7.18")
    lines.append("WALCL,2026-05-28,6512345.0")
    return "\n".join(lines) + "\n"


def main() -> None:
    (FIXTURES / "prices.csv").write_text(gen_prices(), encoding="utf-8")
    (FIXTURES / "fred.csv").write_text(gen_fred(), encoding="utf-8")
    print(f"wrote {FIXTURES / 'prices.csv'} and {FIXTURES / 'fred.csv'}")


if __name__ == "__main__":
    main()
