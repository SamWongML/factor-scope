"""The emerging-list trend gate — a thin-history relaxation that never un-caps a known trend.

A brand-new theme fund has no 200-day MA, so the standard gate reads ``unknown`` and the digest
would abstain on every genuinely new name. For the emerging list only, a trend that is *unknown for
lack of history* falls back to a liquidity + age + valuation read: a liquid, old-enough,
not-overvalued new fund opens; anything else is capped. A *known* trend (open or capped) is always
authoritative — a fund already below its 200-day MA stays capped, the one hard cap intact.
"""

import pytest

from factor_scope.contract import GateState
from factor_scope.emerging import Candidate
from factor_scope.emerging.gate import emerging_gate
from factor_scope.factors import FactorContext
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.unit

_FETCH = "2026-06-05T22:00:00Z"


def _d(i: int) -> str:
    return f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"


def _store(code: str, navs: list[float], pes: list[float] | None = None) -> DuckDBStore:
    store = DuckDBStore(":memory:")
    rows = [
        Reading(series="prices", key=code, as_of=_d(i), fetched_at=_FETCH, payload={"nav": nav})
        for i, nav in enumerate(navs)
    ]
    for i, pe in enumerate(pes or []):
        rows.append(
            Reading(
                series="fundamentals", key=code, as_of=_d(i), fetched_at=_FETCH, payload={"pe": pe}
            )
        )
    store.append(rows)
    return store


def _candidate(code: str, aum: float) -> Candidate:
    return Candidate(
        theme="储能",
        code=code,
        name="储能ETF",
        methodology=0.8,
        fee=0.005,
        aum=aum,
        tracking_error=0.01,
        top10_weight=0.4,
        crowding=0.2,
        as_of="2026-06-05",
    )


def _ctx(store: DuckDBStore, code: str) -> FactorContext:
    return FactorContext(code=code, as_of="2026-12-31", store=store)


_CHEAP_PES = [32.0 - i for i in range(12)]  # decreasing → last is the min → not overvalued
_EXPENSIVE_PES = [10.0 + i for i in range(12)]  # increasing → last is the max → overvalued
_THIN = [1.0 + i * 0.001 for i in range(120)]  # 60 ≤ n < 200 → unknown trend, old enough


def test_thin_history_liquid_and_fair_opens() -> None:
    store = _store("NEW", _THIN, _CHEAP_PES)
    assert emerging_gate(_ctx(store, "NEW"), _candidate("NEW", aum=20.0)) is GateState.OPEN


def test_thin_history_but_overvalued_is_capped() -> None:
    store = _store("NEW", _THIN, _EXPENSIVE_PES)
    assert emerging_gate(_ctx(store, "NEW"), _candidate("NEW", aum=20.0)) is GateState.CAPPED


def test_thin_history_but_illiquid_is_capped() -> None:
    store = _store("NEW", _THIN, _CHEAP_PES)
    assert emerging_gate(_ctx(store, "NEW"), _candidate("NEW", aum=1.0)) is GateState.CAPPED


def test_too_little_history_is_capped_even_if_liquid() -> None:
    store = _store("NEW", [1.0 + i * 0.001 for i in range(40)], _CHEAP_PES)  # < the age floor
    assert emerging_gate(_ctx(store, "NEW"), _candidate("NEW", aum=20.0)) is GateState.CAPPED


def test_missing_pe_is_non_blocking_and_opens() -> None:
    # Missing ≠ bad: no PE → valuation invalid → non-blocking, so a liquid old-enough fund opens.
    store = _store("NEW", _THIN, pes=None)
    assert emerging_gate(_ctx(store, "NEW"), _candidate("NEW", aum=20.0)) is GateState.OPEN


def test_a_known_capped_trend_is_authoritative() -> None:
    # ≥200d of history that ends well below its own 200-day MA → known CAPPED. Liquidity + a cheap
    # valuation must NOT re-open it: the one hard cap is never relaxed away.
    navs = [1.0 + i * 0.01 for i in range(150)] + [2.5 - j * 0.05 for j in range(70)]
    store = _store("OLD", navs, _CHEAP_PES)
    assert emerging_gate(_ctx(store, "OLD"), _candidate("OLD", aum=50.0)) is GateState.CAPPED


def test_a_known_open_trend_is_unchanged() -> None:
    store = _store("OLD", [1.0 + i * 0.005 for i in range(220)], _CHEAP_PES)
    assert emerging_gate(_ctx(store, "OLD"), _candidate("OLD", aum=20.0)) is GateState.OPEN
