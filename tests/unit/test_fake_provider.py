"""Unit tests for the deterministic fake digestion provider.

The fake provider lets the whole bull/bear→synthesis pipeline run end-to-end with no API keys and
no paid calls. It is pure rules over the structured inputs, so the same brief always yields the same
lean — that is what keeps a fixtures run byte-for-byte reproducible.
"""

import pytest

from factor_scope.contract import Band, FactorState, GateState, LeanAction, ListName
from factor_scope.digest import DigestInput, Side, digest_item
from factor_scope.digest.fake import FakeProvider

pytestmark = pytest.mark.unit


def _state(factor: str, level: Band, direction: str = "x", valid: bool = True) -> FactorState:
    return FactorState(factor=factor, level=level, direction=direction, valid=valid)


def _brief(states: list[FactorState], gate: GateState, list_name: ListName) -> DigestInput:
    return DigestInput(
        code="X",
        name="X",
        list_name=list_name,
        states=tuple(states),
        gate=gate,
    )


def test_bearish_holding_leans_trim() -> None:
    # A holding that has stretched up (reversal-DOWN risk) under a tight macro dial leans Trim.
    brief = _brief(
        [
            _state("reversal", Band.HIGH, "stretched up → reversal-DOWN risk"),
            _state("trend gate", Band.HIGH, "uptrend"),
            _state("macro dial", Band.HIGH, "tight"),
        ],
        GateState.OPEN,
        ListName.HOLDINGS,
    )
    result = digest_item(FakeProvider(), brief)
    assert result.action is LeanAction.TRIM
    assert 0.0 <= result.confidence <= 1.0
    assert result.flip_trigger and result.invalidation  # a falsifiable claim


def test_strong_bull_watchlist_leans_buy_early() -> None:
    # A watch name sold off hard (reversal-UP) into an uptrend with an easy macro dial → buy-early.
    brief = _brief(
        [
            _state("reversal", Band.EXTREME_LOW, "sold off hard → reversal-UP potential"),
            _state("trend gate", Band.HIGH, "uptrend"),
            _state("macro dial", Band.LOW, "easy"),
        ],
        GateState.OPEN,
        ListName.WATCHLIST,
    )
    result = digest_item(FakeProvider(), brief)
    assert result.action is LeanAction.BUY_EARLY


def test_fake_is_deterministic() -> None:
    brief = _brief(
        [
            _state("reversal", Band.HIGH, "stretched up → reversal-DOWN risk"),
            _state("trend gate", Band.HIGH, "uptrend"),
            _state("macro dial", Band.HIGH, "tight"),
        ],
        GateState.OPEN,
        ListName.HOLDINGS,
    )
    first = digest_item(FakeProvider(), brief)
    second = digest_item(FakeProvider(), brief)
    assert first == second


def test_bull_and_bear_cases_are_isolated_views_of_the_same_facts() -> None:
    # Consider-the-opposite: each side marshals only its supporting reads from the same brief.
    brief = _brief(
        [
            _state("reversal", Band.EXTREME_LOW, "sold off hard → reversal-UP potential"),
            _state("trend gate", Band.LOW, "downtrend"),
        ],
        GateState.OPEN,
        ListName.HOLDINGS,
    )
    provider = FakeProvider()
    bull = provider.argue(Side.BULL, brief)
    bear = provider.argue(Side.BEAR, brief)
    assert bull.side is Side.BULL and bear.side is Side.BEAR
    assert bull.strength > 0 and bear.strength > 0  # both sides have a real case here
    assert bull.points and bear.points
