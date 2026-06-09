"""Unit tests for the cross-market lead state — the US 13F lead chain.

The leaders' total 13F-disclosed shares are aggregated per as_of into one book-wide accumulation
series; the factor ranks the latest quarter-over-quarter *change* against its own history. A fresh
burst of accumulation confirms demand; a swing to distribution reads chain risk. Book-wide.
Too few quarters degrade to ``valid=False``.
"""

import pytest

from factor_scope.contract import Band
from factor_scope.factors import FactorContext
from factor_scope.factors.battery import cross_market
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.unit


def _ctx(levels: list[float]) -> FactorContext:
    """A single 13F filer disclosing the leader's ``shares`` over successive quarters."""

    store = DuckDBStore(":memory:")
    store.append(
        [
            Reading(
                series="edgar",
                key="0001067983/COHR",
                as_of=f"2025-{1 + i:02d}-28",
                fetched_at="2026-06-05T22:00:00Z",
                payload={"filer": "0001067983", "holding": "COHR", "shares": s},
            )
            for i, s in enumerate(levels)
        ]
    )
    return FactorContext(code="*book*", as_of="2026-12-31", store=store)


def test_fresh_accumulation_reads_lead_confirms() -> None:
    # steady small adds, then a large jump → the latest change ranks at the top
    state = cross_market(_ctx([100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 400.0]))
    assert state.valid is True
    assert state.level in (Band.HIGH, Band.EXTREME_HIGH)
    assert "accumulated" in state.direction


def test_accumulation_burst_lands_extreme_high() -> None:
    # a long run of steady adds, then one outsized quarter → the latest change is the unique
    # top of its own history → strictly EXTREME_HIGH (not merely HIGH).
    state = cross_market(_ctx([100.0 + 10.0 * i for i in range(12)] + [710.0]))
    assert state.level is Band.EXTREME_HIGH


def test_swing_to_distribution_reads_chain_risk() -> None:
    # steady adds, then a sharp sell → the latest change ranks at the bottom
    state = cross_market(_ctx([100.0, 130.0, 160.0, 190.0, 220.0, 250.0, 80.0]))
    assert state.valid is True
    assert state.level in (Band.LOW, Band.EXTREME_LOW)
    assert "chain risk" in state.direction


def test_too_few_quarters_is_invalid_not_an_error() -> None:
    state = cross_market(_ctx([100.0, 110.0, 120.0]))
    assert state.valid is False
