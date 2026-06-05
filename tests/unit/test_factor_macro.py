"""Unit tests for the macro / liquidity dial — one book-wide regime (spec §03)."""

import pytest

from factor_scope.contract import Band
from factor_scope.factors import FactorContext
from factor_scope.factors.battery import macro
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.unit


def _ctx(values: list[float]) -> FactorContext:
    store = DuckDBStore(":memory:")
    store.append(
        [
            Reading(
                series="fred",
                key="DFII10",
                as_of=f"2025-{1 + i:02d}-01",
                fetched_at="2026-06-05T22:00:00Z",
                payload={"series_id": "DFII10", "value": v},
            )
            for i, v in enumerate(values)
        ]
    )
    return FactorContext(code="*book*", as_of="2026-12-31", store=store)


def test_high_real_yield_reads_tight() -> None:
    state = macro(_ctx([0.5, 0.7, 0.9, 1.1, 1.3, 1.5, 1.7, 1.9, 2.1, 2.3, 2.5, 2.8]))
    assert state.valid is True
    assert state.level in (Band.HIGH, Band.EXTREME_HIGH)
    assert "tight" in state.direction


def test_low_real_yield_reads_easy() -> None:
    state = macro(_ctx([2.8, 2.5, 2.3, 2.1, 1.9, 1.7, 1.5, 1.3, 1.1, 0.9, 0.7, 0.4]))
    assert state.valid is True
    assert state.level in (Band.LOW, Band.EXTREME_LOW)
    assert "easy" in state.direction


def test_too_short_history_is_invalid() -> None:
    state = macro(_ctx([1.0, 1.1, 1.2]))
    assert state.valid is False
