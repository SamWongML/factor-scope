"""Unit tests for the valuation state.

The basket's PE (市盈率) ranked against its own history — a stretched multiple is the anti-hype
overvaluation gauge; a depressed one is cheap. Too little history degrades to ``valid=False``.
"""

import pytest

from factor_scope.contract import Band
from factor_scope.factors import FactorContext
from factor_scope.factors.battery import valuation
from factor_scope.factors.window import latest_pe_percentile
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.unit


def _ctx(code: str, pes: list[float]) -> FactorContext:
    store = DuckDBStore(":memory:")
    store.append(
        [
            Reading(
                series="fundamentals",
                key=code,
                as_of=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                fetched_at="2026-06-05T22:00:00Z",
                payload={"pe": pe},
            )
            for i, pe in enumerate(pes)
        ]
    )
    return FactorContext(code=code, as_of="2026-12-31", store=store)


def test_stretched_multiple_reads_expensive() -> None:
    state = valuation(_ctx("HYPE", [30.0 + i for i in range(20)] + [120.0]))
    assert state.valid is True
    assert state.level in (Band.HIGH, Band.EXTREME_HIGH)
    assert "expensive" in state.direction


def test_extreme_multiple_outlier_reads_extreme_high() -> None:
    state = valuation(_ctx("HYPE", [40.0] * 20 + [300.0]))
    assert state.level is Band.EXTREME_HIGH


def test_depressed_multiple_reads_cheap() -> None:
    state = valuation(_ctx("CHEAP", [60.0 - i for i in range(20)] + [12.0]))
    assert state.valid is True
    assert state.level in (Band.LOW, Band.EXTREME_LOW)
    assert "cheap" in state.direction


def test_too_short_history_is_invalid_not_an_error() -> None:
    state = valuation(_ctx("S", [25.0, 26.0, 27.0]))
    assert state.valid is False


def test_latest_pe_percentile_needs_enough_prints() -> None:
    # The shared valuation read (the factor and the anti-hype guardrails both rank with it):
    # below the print floor there is no read at all.
    assert latest_pe_percentile([float(i) for i in range(11)]) is None


def test_latest_pe_percentile_ranks_the_latest_print_against_its_own_history() -> None:
    # 12 ascending PEs ending at the maximum → mid-rank (11 + 0.5) / 12 ≈ 0.958, an extreme read.
    assert latest_pe_percentile([float(i) for i in range(12)]) == pytest.approx(11.5 / 12)
