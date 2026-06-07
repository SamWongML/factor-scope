"""Unit tests for the reversal state.

Short-horizon return ranked vs its own history. In A-shares a stretched up-move is a
reversal-**DOWN** risk; a hard sell-off is reversal-**UP** potential.
"""

import pytest

from factor_scope.contract import Band
from factor_scope.factors import FactorContext
from factor_scope.factors.battery import reversal
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.unit


def _ctx(code: str, navs: list[float]) -> FactorContext:
    store = DuckDBStore(":memory:")
    store.append(
        [
            Reading(
                series="prices",
                key=code,
                as_of=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                fetched_at="2026-06-05T22:00:00Z",
                payload={"nav": nav},
            )
            for i, nav in enumerate(navs)
        ]
    )
    return FactorContext(code=code, as_of="2026-12-31", store=store)


def test_sharp_recent_runup_is_reversal_down_risk() -> None:
    navs = [1.0 + 0.001 * i for i in range(80)] + [1.08 + 0.05 * j for j in range(20)]
    state = reversal(_ctx("UP", navs))
    assert state.valid is True
    assert state.level in (Band.HIGH, Band.EXTREME_HIGH)
    assert "DOWN" in state.direction


def test_sharp_recent_selloff_is_reversal_up_potential() -> None:
    navs = [2.0 - 0.001 * i for i in range(80)] + [1.92 - 0.05 * j for j in range(20)]
    state = reversal(_ctx("DN", navs))
    assert state.valid is True
    assert state.level in (Band.LOW, Band.EXTREME_LOW)
    assert "UP" in state.direction


def test_too_short_series_is_invalid_not_an_error() -> None:
    state = reversal(_ctx("S", [1.0, 1.1, 1.2, 1.3]))
    assert state.valid is False
