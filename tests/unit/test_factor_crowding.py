"""Unit tests for the crowding state.

Daily turnover (换手率) ranked against the fund's own history. A hot, over-traded product reads
crowded (a crash-risk gauge); a quiet one reads light. Too little history degrades to invalid.
"""

import pytest

from factor_scope.contract import Band
from factor_scope.factors import FactorContext
from factor_scope.factors.battery import crowding
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.unit


def _ctx(code: str, turnovers: list[float]) -> FactorContext:
    store = DuckDBStore(":memory:")
    store.append(
        [
            Reading(
                series="trading_activity",
                key=code,
                as_of=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                fetched_at="2026-06-05T22:00:00Z",
                payload={"turnover": t, "amount": 1.0},
            )
            for i, t in enumerate(turnovers)
        ]
    )
    return FactorContext(code=code, as_of="2026-12-31", store=store)


def test_top_turnover_reads_crowded() -> None:
    state = crowding(_ctx("HOT", [1.0 + 0.1 * i for i in range(20)] + [9.0]))
    assert state.valid is True
    assert state.level in (Band.HIGH, Band.EXTREME_HIGH)
    assert "crowded" in state.direction


def test_extreme_turnover_outlier_reads_extreme_high() -> None:
    state = crowding(_ctx("HOT", [2.0] * 20 + [50.0]))
    assert state.level is Band.EXTREME_HIGH


def test_light_turnover_reads_quiet() -> None:
    state = crowding(_ctx("COLD", [9.0 - 0.1 * i for i in range(20)] + [0.2]))
    assert state.valid is True
    assert state.level in (Band.LOW, Band.EXTREME_LOW)
    assert "quiet" in state.direction


def test_too_short_history_is_invalid_not_an_error() -> None:
    state = crowding(_ctx("S", [1.0, 2.0, 3.0]))
    assert state.valid is False
