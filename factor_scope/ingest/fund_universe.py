"""The full CN fund universe — every fund's identity, lifecycle, and scorecard inputs.

`fund_universe.csv → {code, name, type, on_exchange, inception, delisting, fee, tracking_error,
top10_weight}`. This is the book the engine reasons over once theme→fund mapping (a later step)
replaces the hand-curated list: all funds, not just the held ones. Each row is keyed by code and
stamped with the run's ``as_of`` (universe membership is point-in-time — a delisted fund is kept
with its ``delisting`` date so the look-through stays survivorship-aware). The per-fund scorecard
inputs (fee, tracking error, top-10 weight) may be absent for a fund that does not disclose them;
a missing input degrades the row to ``valid=False`` rather than dropping it.

Live merges AkShare's ``fund_name_em`` (all funds) with ``fund_etf_spot_em`` (the on-exchange ETF
universe) — opt-in, never called in CI.
"""

from __future__ import annotations

from pathlib import Path

from factor_scope.ingest.base import optional_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "fund_universe"
FIXTURE = "fund_universe.csv"
_REQUIRED = (
    "code",
    "name",
    "type",
    "on_exchange",
    "inception",
    "delisting",
    "fee",
    "tracking_error",
    "top10_weight",
)
_SCORECARD = ("fee", "tracking_error", "top10_weight")


def parse(text: str, *, as_of: str, fetched_at: str) -> list[Reading]:
    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        code = required_str(row, "code", line_no, SERIES)
        scorecard = {f: optional_float(row, f, line_no, SERIES) for f in _SCORECARD}
        readings.append(
            Reading(
                series=SERIES,
                key=code,
                as_of=as_of,
                fetched_at=fetched_at,
                payload={
                    "name": required_str(row, "name", line_no, SERIES),
                    "type": required_str(row, "type", line_no, SERIES),
                    "on_exchange": (row.get("on_exchange") or "").strip().lower() == "true",
                    "inception": (row.get("inception") or "").strip(),
                    "delisting": (row.get("delisting") or "").strip(),
                    **scorecard,
                    "valid": all(v is not None for v in scorecard.values()),
                },
            )
        )
    return readings


def load_fixture(path: Path, *, as_of: str, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), as_of=as_of, fetched_at=fetched_at)


def fetch_live(*, as_of: str, fetched_at: str) -> list[Reading]:  # pragma: no cover - opt-in
    """Merge AkShare's all-funds list with the on-exchange ETF universe (needs `live` + network)."""

    import akshare as ak

    on_exchange = {str(c) for c in ak.fund_etf_spot_em()["代码"]}
    readings: list[Reading] = []
    for _, row in ak.fund_name_em().iterrows():
        code = str(row["基金代码"])
        readings.append(
            Reading(
                series=SERIES,
                key=code,
                as_of=as_of,
                fetched_at=fetched_at,
                payload={
                    "name": str(row["基金简称"]),
                    "type": str(row["基金类型"]),
                    "on_exchange": code in on_exchange,
                    "inception": "",
                    "delisting": "",
                    "fee": None,
                    "tracking_error": None,
                    "top10_weight": None,
                    "valid": False,
                },
            )
        )
    return readings
