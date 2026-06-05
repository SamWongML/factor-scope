"""Unit tests for the low-vol / drawdown regime state (spec §03)."""

import pytest

from factor_scope.contract import Band
from factor_scope.factors import FactorContext
from factor_scope.factors.battery import low_vol
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


def test_calm_then_violent_tail_reads_elevated_vol() -> None:
    calm = [1.0 + 0.0005 * i for i in range(80)]
    violent = [calm[-1] * (1.1 if j % 2 else 0.9) for j in range(20)]
    state = low_vol(_ctx("V", calm + violent))
    assert state.valid is True
    assert state.level in (Band.HIGH, Band.EXTREME_HIGH)
    assert "stressed" in state.direction or "elevated" in state.direction


def test_drawdown_depth_is_reported_in_evidence() -> None:
    navs = [1.0 + 0.01 * i for i in range(100)] + [2.0 - 0.02 * j for j in range(100)]
    state = low_vol(_ctx("D", navs))
    assert state.valid is True
    assert state.evidence is not None and "drawdown" in state.evidence


def test_too_short_series_is_invalid() -> None:
    state = low_vol(_ctx("S", [1.0, 1.1, 1.2]))
    assert state.valid is False
