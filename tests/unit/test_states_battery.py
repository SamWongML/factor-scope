"""The battery as a whole: 8 states per item, missing inputs degrade to valid=False.

The contract is "a handful is enough; a failed/stale factor → valid:false and is ignored". All eight
states compute from the bundled snapshot — the per-item reads (trend gate, reversal, low-vol,
crowding, valuation) from a code's own price/turnover/PE history, the book-wide reads (macro,
cross-market lead, demand) from one shared series. An unknown code keeps the book-wide states and
degrades only the per-item ones — never dropped, never raised.
"""

import pytest

from factor_scope.config import Config
from factor_scope.factors import FACTOR_NAMES, FactorContext, compute_states
from factor_scope.markets import get_market
from factor_scope.store import DuckDBStore

pytestmark = pytest.mark.unit


def _fixture_store() -> DuckDBStore:
    store = DuckDBStore(":memory:")
    store.append(get_market("ashare").gather(Config(), as_of="2026-06-05"))
    return store


def test_battery_emits_all_eight_states_in_spec_order() -> None:
    store = _fixture_store()
    ctx = FactorContext(code="561010", as_of="2026-06-05", store=store)
    states = compute_states(ctx)
    assert [s.factor for s in states] == list(FACTOR_NAMES)
    assert len(states) == 8


def test_every_factor_is_data_backed_on_the_bundled_book() -> None:
    store = _fixture_store()
    ctx = FactorContext(code="561010", as_of="2026-06-05", store=store)
    by_name = {s.factor: s for s in compute_states(ctx)}
    for name in FACTOR_NAMES:
        assert by_name[name].valid is True, name


def test_missing_code_never_raises_and_keeps_only_book_wide_states() -> None:
    store = _fixture_store()
    ctx = FactorContext(code="does-not-exist", as_of="2026-06-05", store=store)
    by_name = {s.factor: s for s in compute_states(ctx)}  # must not raise
    # Per-item reads need this code's own history, so they degrade…
    for name in ("trend gate", "reversal", "low-vol/drawdown", "crowding", "valuation"):
        assert by_name[name].valid is False, name
    # …but the book-wide regimes hold for any code.
    for name in ("macro dial", "cross-market lead", "demand"):
        assert by_name[name].valid is True, name
