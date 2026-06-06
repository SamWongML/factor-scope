"""Prices / fund-NAV adapter (CN). Fixture: `prices.csv → {code, as_of, nav}`.

Per-item gain comes from cost basis vs the current NAV pulled here. Live is AkShare's ETF history
(``fund_etf_hist_em``) — opt-in, never called in CI.
"""

from __future__ import annotations

from pathlib import Path

from factor_scope.ingest.base import IngestError, as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "prices"
FIXTURE = "prices.csv"
_REQUIRED = ("code", "as_of", "nav")

# CN prices are dual-sourced (AkShare + Baostock) so one scraper going offline can't kill a run.
# Two independent reads of the same NAV within this fraction corroborate each other (L1 / §04).
_CORROBORATION_TOLERANCE = 0.01


def parse(text: str, *, fetched_at: str) -> list[Reading]:
    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        code = required_str(row, "code", line_no, SERIES)
        as_of = required_str(row, "as_of", line_no, SERIES)
        nav = as_float(row, "nav", line_no, SERIES)
        readings.append(
            Reading(
                series=SERIES, key=code, as_of=as_of, fetched_at=fetched_at, payload={"nav": nav}
            )
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)


def select_corroborated(
    primary: list[Reading],
    secondary: list[Reading],
    *,
    tolerance: float = _CORROBORATION_TOLERANCE,
) -> list[Reading]:
    """Cross-validate the AkShare (``primary``) NAV against the Baostock (``secondary``) NAV.

    The CN price path is dual-sourced for anti-fragility (L1 / §04). This is the selection policy:

    - **Fall back** — when ``primary`` is empty (AkShare blocked or offline), substitute the
      ``secondary`` Baostock read so the run still has a price.
    - **Corroborate** — when both sources read the *same trading day's* NAV (within ``tolerance``),
      trust the AkShare read.
    - **Surface conflicts** — when both are present for the same day but disagree beyond
      ``tolerance``, raise rather than silently pick one: two independent sources that materially
      diverge is a data-quality signal a human should see.

    The cross-check is gated on a matching ``as_of``: a stale-but-working Baostock read (an earlier
    session) is never compared against a fresh AkShare read, so a normal day-over-day move can't
    spuriously raise. When only the ``primary`` is present there is nothing to corroborate against,
    so it is returned as-is.
    """

    if not primary:
        return secondary
    p, s = primary[-1], secondary[-1] if secondary else None
    if s is not None and p.as_of == s.as_of:
        a, b = p.payload["nav"], s.payload["nav"]
        if a and abs(a - b) / abs(a) > tolerance:
            raise IngestError(
                f"{SERIES}: AkShare ({a}) and Baostock ({b}) disagree beyond {tolerance:.0%}"
            )
    return primary


def fetch_live(code: str, *, fetched_at: str) -> list[Reading]:  # pragma: no cover - opt-in
    """Pull the latest daily NAV for one ETF via AkShare. Requires the `live` extra + network."""

    import akshare as ak

    frame = ak.fund_etf_hist_em(symbol=code, period="daily", adjust="")
    last = frame.iloc[-1]
    return [
        Reading(
            series=SERIES,
            key=code,
            as_of=str(last["日期"]),
            fetched_at=fetched_at,
            payload={"nav": float(last["收盘"])},
        )
    ]
