"""The battery as a whole: 8 states per item, missing inputs degrade to valid=False (spec §03).

The contract is "a handful is enough; a failed/stale factor → valid:false and is ignored". The
data-backed states (trend gate, reversal, low-vol, macro) compute from the store; the ones whose
inputs are not yet ingested (cross-market, crowding, demand, valuation) are present but invalid —
never dropped, never raised.
"""

import pytest

from factor_scope.config import Config
from factor_scope.factors import FACTOR_NAMES, FactorContext, compute_states
from factor_scope.ingest import gather_fixture_readings
from factor_scope.store import DuckDBStore

pytestmark = pytest.mark.unit


def _fixture_store() -> DuckDBStore:
    store = DuckDBStore(":memory:")
    store.append(gather_fixture_readings(Config(), as_of="2026-06-05"))
    return store


def test_battery_emits_all_eight_states_in_spec_order() -> None:
    store = _fixture_store()
    ctx = FactorContext(code="561010", as_of="2026-06-05", store=store)
    states = compute_states(ctx)
    assert [s.factor for s in states] == list(FACTOR_NAMES)
    assert len(states) == 8


def test_data_backed_states_are_valid_unbacked_are_invalid() -> None:
    store = _fixture_store()
    ctx = FactorContext(code="561010", as_of="2026-06-05", store=store)
    by_name = {s.factor: s for s in compute_states(ctx)}
    for name in ("trend gate", "reversal", "low-vol/drawdown", "macro dial"):
        assert by_name[name].valid is True, name
    for name in ("cross-market lead", "crowding", "demand", "valuation"):
        assert by_name[name].valid is False, name


def test_missing_code_never_raises_and_is_all_invalid_price_states() -> None:
    store = _fixture_store()
    ctx = FactorContext(code="does-not-exist", as_of="2026-06-05", store=store)
    states = compute_states(ctx)  # must not raise
    by_name = {s.factor: s for s in states}
    assert by_name["trend gate"].valid is False
    assert by_name["reversal"].valid is False
    # The macro dial is book-wide, so it is still valid even for an unknown code.
    assert by_name["macro dial"].valid is True
