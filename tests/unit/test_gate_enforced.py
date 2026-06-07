"""Unit tests for the hard trend gate in the digest (principle #4).

A capped gate (price below the 200-day MA) hard-caps the lean at Hold/Avoid. Nothing — not a
bullish state battery, not a misbehaving provider, not the scorecard — may open it. These tests
enforce that the orchestrator caps the lean *regardless of what the provider proposes*.
"""

import pytest

from factor_scope.contract import (
    Band,
    FactorState,
    GateState,
    LeanAction,
    ListName,
    ReliabilityBucket,
    Scorecard,
)
from factor_scope.digest import Case, DigestInput, Proposal, Side, digest_item
from factor_scope.digest.fake import FakeProvider

pytestmark = pytest.mark.unit


def _state(factor: str, level: Band, direction: str = "x", valid: bool = True) -> FactorState:
    return FactorState(factor=factor, level=level, direction=direction, valid=valid)


class _BullishStub:
    """A misbehaving provider that always wants to buy, with full conviction."""

    name = "stub"

    def argue(self, side: Side, brief: DigestInput) -> Case:
        # A one-sided cheerleader: a strong bull case, no bear case (so it never abstains).
        strength = 3.0 if side is Side.BULL else 0.0
        return Case(side=side, strength=strength, confidence=0.95, points=("everything is great",))

    def synthesize(self, brief: DigestInput, bull: Case, bear: Case) -> Proposal:
        return Proposal(action=LeanAction.BUY_EARLY, confidence=0.95, rationale=("buy!",))


def _bullish_brief(list_name: ListName, gate: GateState) -> DigestInput:
    return DigestInput(
        code="X",
        name="X",
        list_name=list_name,
        states=(
            _state("reversal", Band.EXTREME_LOW, "sold off hard → reversal-UP potential"),
            _state("trend gate", Band.LOW, "downtrend"),
        ),
        gate=gate,
    )


def test_capped_gate_caps_a_misbehaving_provider() -> None:
    # The provider screams buy; the capped gate forbids any bullish lean.
    watch = digest_item(_BullishStub(), _bullish_brief(ListName.WATCHLIST, GateState.CAPPED))
    assert watch.action is LeanAction.AVOID
    held = digest_item(_BullishStub(), _bullish_brief(ListName.HOLDINGS, GateState.CAPPED))
    assert held.action is LeanAction.HOLD
    assert watch.action is not LeanAction.BUY_EARLY


def test_open_gate_lets_a_bullish_call_through() -> None:
    watch = digest_item(_BullishStub(), _bullish_brief(ListName.WATCHLIST, GateState.OPEN))
    assert watch.action is LeanAction.BUY_EARLY


def test_scorecard_cannot_open_a_capped_gate() -> None:
    # Even a glowing scorecard (which only ever touches the confidence number) cannot un-cap.
    brief = DigestInput(
        code="X",
        name="X",
        list_name=ListName.WATCHLIST,
        states=(
            _state("reversal", Band.EXTREME_LOW, "sold off hard → reversal-UP potential"),
            _state("trend gate", Band.LOW, "downtrend"),
        ),
        gate=GateState.CAPPED,
        scorecard=Scorecard(
            window="60d",
            n=20,
            reliability=[ReliabilityBucket(bucket=0.9, realised=1.0, note="underconfident")],
        ),
    )
    result = digest_item(FakeProvider(), brief)
    assert result.action is not LeanAction.BUY_EARLY
