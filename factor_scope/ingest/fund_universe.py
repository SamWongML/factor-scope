"""The full CN fund universe — every fund's identity, lifecycle, and scorecard inputs.

The universe read carries ``{code, name, type, on_exchange, inception, delisting, fee,
tracking_error, top10_weight}`` per fund. This is the book the engine reasons over once the
theme→fund mapping replaces the hand-curated list: all funds, not just the held ones. Each row
is keyed by code and stamped with the run's ``as_of`` (universe membership is point-in-time — a
delisted fund is kept with its ``delisting`` date so the look-through stays survivorship-aware). The
per-fund scorecard inputs (fee, tracking error, top-10 weight) may be absent for a fund that does
not disclose them; a missing input degrades the row to ``valid=False`` rather than dropping it. The
mapping from the universe read to a ``Reading`` lives in the feed (``factor_scope.ingest.feed``).

Live merges AkShare's ``fund_name_em`` (all funds) with ``fund_etf_spot_em`` (the on-exchange ETF
universe) — never called in CI; the delisting machinery below has no live feed (a dead fund simply
vanishes from the next pull).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from factor_scope.store import Reading

SERIES = "fund_universe"

Tier = Literal["core", "probation", "dead"]

# Universe tiers decide who pays the per-fund deep fetch each night. Only **core** funds get
# holdings/activity/valuation + deep NAV history; **probation** (small/new/improving) is re-screened
# nightly from the already-fetched spot board at zero network cost and promoted on its own numbers;
# **dead** is recorded for audit but never fetched. This caps the universe-wide burst that trips the
# history host while keeping every fund perpetually visible to the cheap screen — so an uncrowded
# fund is never made permanently invisible by a one-off liquidity dip.
#
# The bars reuse the emerging funnel's investability vocabulary: ``CORE_AUM_FLOOR`` is its 5亿
# closure/illiquidity floor, ``SEASONING_DAYS`` its launch-seasoning window — so the fetch gate and
# the candidate-quality gate can never silently disagree on "investable". ``DEAD_AUM_CEILING`` is
# the CSRC zombie-fund clearance trigger (5000万). Liquidity is the spot board's daily 成交额 in 亿.
CORE_AUM_FLOOR = 5.0  # 亿元: at/above this a seasoned, liquid fund earns the deep-fetch tier
CORE_AMOUNT_FLOOR = 0.5  # 亿元/day traded value (成交额): the liquidity leg of core admission
DEAD_AUM_CEILING = 0.5  # 亿元: the CSRC zombie-fund clearance floor (5000万) — under it a fund dies
DEAD_AMOUNT_FLOOR = 0.01  # 亿元/day: near-zero turnover; a dead fund is both tiny *and* untraded
SEASONING_DAYS = 180  # a fund younger than this stays in probation regardless of size


def _age_days(inception: str | None, as_of: str) -> int | None:
    """A fund's age in days at the run date, or ``None`` when undisclosed/unparseable/future-dated.

    An unparseable or future launch date is no evidence of age — degrade, never raise (mirrors the
    emerging funnel's seasoning read).
    """

    if not inception:
        return None
    try:
        age = (date.fromisoformat(as_of) - date.fromisoformat(inception)).days
    except ValueError:
        return None
    return age if age >= 0 else None


def classify_tier(
    *, aum: float | None, amount: float | None, inception: str | None, as_of: str
) -> Tier:
    """Sort one on-exchange ETF into core / probation / dead from cheap spot-board fields only.

    ``aum`` is total assets in 亿元 and ``amount`` is the day's traded value (成交额) in 亿元 — both
    carried on the once-per-run spot board, so the whole universe is re-classified with no per-fund
    call. The rules degrade safely: a fresh listing (< ``SEASONING_DAYS``) or a fund with a missing
    size/liquidity disclosure stays in **probation** (never gated on size, never inferred dead);
    only a *seasoned* fund that is both sub-floor in AUM and effectively untraded is **dead**.
    """

    age = _age_days(inception, as_of)
    if age is not None and age < SEASONING_DAYS:
        return "probation"  # never gate a fresh listing on size — keep the early, uncrowded window
    if aum is None or amount is None:
        return "probation"  # absence is not evidence of death — keep re-screening it
    if aum >= CORE_AUM_FLOOR and amount >= CORE_AMOUNT_FLOOR:
        return "core"
    if aum < DEAD_AUM_CEILING and amount < DEAD_AMOUNT_FLOOR:
        return "dead"
    return "probation"


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
