"""The full CN fund universe — every fund's identity, lifecycle, and scorecard inputs.

`fund_universe.csv → {code, name, type, on_exchange, inception, delisting, fee, tracking_error,
top10_weight}`. This is the book the engine reasons over once theme→fund mapping (a later step)
replaces the hand-curated list: all funds, not just the held ones. Each row is keyed by code and
stamped with the run's ``as_of`` (universe membership is point-in-time — a delisted fund is kept
with its ``delisting`` date so the look-through stays survivorship-aware). The per-fund scorecard
inputs (fee, tracking error, top-10 weight) may be absent for a fund that does not disclose them;
a missing input degrades the row to ``valid=False`` rather than dropping it.

Live merges AkShare's ``fund_name_em`` (all funds) with ``fund_etf_spot_em`` (the on-exchange ETF
universe) — never called in CI.
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


def delisting_disclosures(
    universe: list[Reading], *, as_of: str, fetched_at: str
) -> list[Reading]:
    """Disclose every fund the feed has stopped listing as delisted at ``as_of``.

    There is no live delisting feed — a dead fund simply vanishes from the next universe pull, and
    in an append-only store its last row would read "listed" forever. Given the latest row per fund
    (the post-ingest point-in-time read), a fund whose row predates this run and carries no
    delisting date was dropped by the feed tonight, so one disclosure row dated ``as_of`` is
    appended; reads at earlier dates still see it alive (survivorship-aware both ways). A feed that
    returned *nothing* tonight discloses nothing — an outage is no evidence of death — and a fund
    the feed re-lists later reads as listed again from its fresh row. Re-runs are no-ops: a
    disclosure is itself stamped ``as_of`` and carries a delisting date.
    """

    if not any(r.as_of == as_of for r in universe):
        return []
    return [
        Reading(
            series=SERIES,
            key=r.key,
            as_of=as_of,
            fetched_at=fetched_at,
            payload={**r.payload, "delisting": as_of},
        )
        for r in universe
        if r.as_of < as_of and not r.payload.get("delisting")
    ]


def still_listed(delisting: str, as_of: str) -> bool:
    """Was the fund tradable at ``as_of``? (the survivorship-aware membership test)

    Empty ``delisting`` means listed. A fund is excluded from its delisting day onward, but a
    point-in-time query *before* that day keeps it — the universe at an old ``as_of`` must include
    the funds that have since died, or every backward look inherits survivorship bias.
    """

    return not delisting or delisting > as_of


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


def fetch_live(*, as_of: str, fetched_at: str) -> list[Reading]:  # pragma: no cover - live path
    """Merge AkShare's all-funds list with the on-exchange ETF universe (needs `live` + network).

    The exchange-traded ranking (``fund_exchange_rank_em``) carries each on-exchange fund's
    inception (成立日期) — the launch-at-peak guardrail's input — in one bulk call; an off-exchange
    fund keeps an empty inception (missing data never vetoes). Delisting dates are not disclosed by
    any feed; they are captured at ingest by :func:`delisting_disclosures` when a fund vanishes.
    """

    import akshare as ak
    import pandas as pd

    on_exchange = {str(c) for c in ak.fund_etf_spot_em()["代码"]}
    inceptions = {
        str(row["基金代码"]): f"{row['成立日期']:%Y-%m-%d}"
        for _, row in ak.fund_exchange_rank_em().iterrows()
        if pd.notna(row["成立日期"])
    }
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
                    "inception": inceptions.get(code, ""),
                    "delisting": "",
                    "fee": None,
                    "tracking_error": None,
                    "top10_weight": None,
                    "valid": False,
                },
            )
        )
    return readings
