"""Unit tests for the demand state — one book-wide end-demand dial.

End-demand orders/capex revisions ranked against their own history: accelerating revisions read a
demand tailwind, fading ones a headwind. Book-wide, so it does not depend on a single fund's code.
Too little history degrades to ``valid=False``.
"""

import pytest

from factor_scope.contract import Band
from factor_scope.factors import FactorContext
from factor_scope.factors.battery import demand
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.unit


def _ctx(revisions: list[float]) -> FactorContext:
    store = DuckDBStore(":memory:")
    store.append(
        [
            Reading(
                series="demand",
                key="end_demand",
                as_of=f"2025-{1 + i:02d}-01",
                fetched_at="2026-06-05T22:00:00Z",
                payload={"revision": r},
            )
            for i, r in enumerate(revisions)
        ]
    )
    return FactorContext(code="*book*", as_of="2026-12-31", store=store)


def test_accelerating_revisions_read_tailwind() -> None:
    state = demand(_ctx([0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.18]))
    assert state.valid is True
    assert state.level in (Band.HIGH, Band.EXTREME_HIGH)
    assert "tailwind" in state.direction


def test_fading_revisions_read_headwind() -> None:
    state = demand(_ctx([0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01, -0.10]))
    assert state.valid is True
    assert state.level in (Band.LOW, Band.EXTREME_LOW)
    assert "headwind" in state.direction


def test_demand_collapse_lands_extreme_low() -> None:
    # steady small revisions, then a sharp collapse → the latest revision is the unique bottom of
    # its own history → strictly EXTREME_LOW (not merely LOW).
    state = demand(_ctx([0.01 * i for i in range(11)] + [-0.50]))
    assert state.level is Band.EXTREME_LOW


def test_too_short_history_is_invalid_not_an_error() -> None:
    state = demand(_ctx([0.02, 0.03, 0.04]))
    assert state.valid is False
