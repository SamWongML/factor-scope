"""Unit tests for the 200-day trend gate (spec §03/§08) — the one hard cap.

Below the 200-day MA → ``capped`` (lean capped at Hold/Avoid in Phase 5; nothing may open it).
Above → ``open``. Too little history → ``unknown`` (never raises).
"""

import pytest

from factor_scope.contract import Band, GateState
from factor_scope.factors import FactorContext, compute_gate
from factor_scope.factors.battery import trend_gate
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.unit


def _store_with(code: str, navs: list[float]) -> DuckDBStore:
    store = DuckDBStore(":memory:")
    rows = [
        Reading(
            series="prices",
            key=code,
            as_of=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
            fetched_at="2026-06-05T22:00:00Z",
            payload={"nav": nav},
        )
        for i, nav in enumerate(navs)
    ]
    store.append(rows)
    return store


def test_below_200ma_caps_the_gate() -> None:
    # Rises to a peak then bleeds well under its own 200-day average.
    navs = [1.0 + i * 0.01 for i in range(150)] + [2.5 - j * 0.05 for j in range(70)]
    store = _store_with("X", navs)
    ctx = FactorContext(code="X", as_of="2026-12-31", store=store)
    assert compute_gate(ctx) is GateState.CAPPED
    state = trend_gate(ctx)
    assert state.valid is True
    assert "downtrend" in state.direction


def test_above_200ma_opens_the_gate() -> None:
    navs = [1.0 + i * 0.005 for i in range(220)]  # steady uptrend
    store = _store_with("Y", navs)
    ctx = FactorContext(code="Y", as_of="2026-12-31", store=store)
    assert compute_gate(ctx) is GateState.OPEN
    state = trend_gate(ctx)
    assert state.valid is True
    assert state.level in (Band.HIGH, Band.EXTREME_HIGH)
    assert "uptrend" in state.direction


def test_insufficient_history_is_unknown_not_an_error() -> None:
    store = _store_with("Z", [1.0, 1.1, 1.2])  # far fewer than 200
    ctx = FactorContext(code="Z", as_of="2026-12-31", store=store)
    assert compute_gate(ctx) is GateState.UNKNOWN
    state = trend_gate(ctx)
    assert state.valid is False
