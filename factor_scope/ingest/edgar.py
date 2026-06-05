"""US lead-chain adapter (EDGAR 13F / N-PORT). `edgar.csv → {filer, as_of, holding, shares}`.

The US-side holdings that feed the cross-market lead and the connection graph. Each ``(filer,
holding)`` pair is its own point-in-time key. Live is EdgarTools — opt-in, never called in CI.
"""

from __future__ import annotations

from pathlib import Path

from factor_scope.ingest.base import as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "edgar"
FIXTURE = "edgar.csv"
_REQUIRED = ("filer", "as_of", "holding", "shares")


def parse(text: str, *, fetched_at: str) -> list[Reading]:
    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        filer = required_str(row, "filer", line_no, SERIES)
        as_of = required_str(row, "as_of", line_no, SERIES)
        holding = required_str(row, "holding", line_no, SERIES)
        shares = as_float(row, "shares", line_no, SERIES)
        readings.append(
            Reading(
                series=SERIES,
                key=f"{filer}/{holding}",
                as_of=as_of,
                fetched_at=fetched_at,
                payload={"filer": filer, "holding": holding, "shares": shares},
            )
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)


def fetch_live(cik: str, *, fetched_at: str) -> list[Reading]:  # pragma: no cover - opt-in
    """Pull a filer's latest 13F holdings via EdgarTools. Requires the `live` extra + network."""

    from edgar import Company

    filing = Company(cik).get_filings(form="13F-HR").latest(1)
    table = filing.obj().infotable
    as_of = str(filing.filing_date)
    readings: list[Reading] = []
    for _, row in table.iterrows():
        readings.append(
            Reading(
                series=SERIES,
                key=f"{cik}/{row['Ticker']}",
                as_of=as_of,
                fetched_at=fetched_at,
                payload={
                    "filer": cik,
                    "holding": str(row["Ticker"]),
                    "shares": float(row["Shares"]),
                },
            )
        )
    return readings
