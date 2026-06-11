"""De-biasing the synthesis seat against position bias.

The synthesis seat is run in *both* presentation orders (bull-first and bear-first) and the two are
averaged: an order-sensitive model cannot tilt the call by seat order, the de-biased confidence is
invariant to which order we label "first", and a call that flips *sides* under the swap is too
position-biased to trust and abstains. ``order_residual`` records how far apart the two orders were.
"""

from __future__ import annotations

import pytest

from factor_scope.contract import Band, FactorState, GateState, LeanAction, ListName
from factor_scope.digest import Case, DigestInput, Proposal, Side, digest_item

pytestmark = pytest.mark.unit

# The synthesis seat is run in both presentation orders and averaged; the de-biased confidence must
# be invariant to which order we call "first" to within this — the position-bias acceptance bar.
ORDER_SWAP_TOL = 0.05


def _brief(list_name: ListName = ListName.WATCHLIST) -> DigestInput:
    return DigestInput(
        code="X",
        name="X",
        list_name=list_name,
        states=(
            FactorState(factor="reversal", level=Band.HIGH, direction="stretched"),
            FactorState(factor="macro dial", level=Band.HIGH, direction="tight"),
        ),
        gate=GateState.OPEN,
    )


class _OrderBiased:
    """A position-biased synthesis: its confidence depends on which case is presented first."""

    name = "stub"

    def __init__(self, *, bull_first: float, bear_first: float) -> None:
        self._bull_first = bull_first
        self._bear_first = bear_first

    def argue(self, side: Side, brief: DigestInput) -> Case:
        return Case(side=side, strength=2.0 if side is Side.BULL else 0.0, confidence=0.6)

    def seats(self, brief: DigestInput) -> tuple[Case, Case]:
        return self.argue(Side.BULL, brief), self.argue(Side.BEAR, brief)

    def synthesize(
        self, brief: DigestInput, bull: Case, bear: Case, *, present_bear_first: bool = False
    ) -> Proposal:
        conf = self._bear_first if present_bear_first else self._bull_first
        return Proposal(action=LeanAction.HOLD, confidence=conf)


def test_debiased_confidence_is_invariant_to_which_order_is_first() -> None:
    # One model inflates when the bull is shown first; its mirror inflates when the bear is first.
    # Because the orchestrator runs *both* orders and averages, the de-biased confidence matches.
    bull_biased = _OrderBiased(bull_first=0.8, bear_first=0.4)
    bear_biased = _OrderBiased(bull_first=0.4, bear_first=0.8)
    a = digest_item(bull_biased, _brief())
    b = digest_item(bear_biased, _brief())
    assert abs(a.confidence - b.confidence) <= ORDER_SWAP_TOL
    assert a.order_residual == pytest.approx(b.order_residual)


def test_order_residual_records_the_position_bias() -> None:
    result = digest_item(_OrderBiased(bull_first=0.8, bear_first=0.4), _brief())
    assert result.order_residual == pytest.approx(0.4)
    assert result.bull_strength == 2.0 and result.bear_strength == 0.0


class _SideFlip:
    """A model whose *side* flips with order: bull-first → buy, bear-first → avoid."""

    name = "stub"

    def argue(self, side: Side, brief: DigestInput) -> Case:
        return Case(side=side, strength=2.0 if side is Side.BULL else 0.0, confidence=0.6)

    def seats(self, brief: DigestInput) -> tuple[Case, Case]:
        return self.argue(Side.BULL, brief), self.argue(Side.BEAR, brief)

    def synthesize(
        self, brief: DigestInput, bull: Case, bear: Case, *, present_bear_first: bool = False
    ) -> Proposal:
        action = LeanAction.AVOID if present_bear_first else LeanAction.BUY_EARLY
        return Proposal(action=action, confidence=0.7)


def test_a_call_that_flips_sides_under_the_swap_abstains() -> None:
    # The lean flips bullish↔bearish purely by seat order — too position-biased to trust → abstain.
    result = digest_item(_SideFlip(), _brief())
    assert result.action is LeanAction.ABSTAIN
    assert result.confidence == 0.0


class _RubricStub:
    """A synthesis scoring one criterion differently per order — to test rubric averaging."""

    name = "stub"

    def argue(self, side: Side, brief: DigestInput) -> Case:
        return Case(side=side, strength=2.0 if side is Side.BULL else 0.0, confidence=0.6)

    def seats(self, brief: DigestInput) -> tuple[Case, Case]:
        return self.argue(Side.BULL, brief), self.argue(Side.BEAR, brief)

    def synthesize(
        self, brief: DigestInput, bull: Case, bear: Case, *, present_bear_first: bool = False
    ) -> Proposal:
        score = 0.2 if present_bear_first else 0.8
        return Proposal(action=LeanAction.HOLD, confidence=0.6, rubric=(("valuation", score),))


def test_rubric_is_averaged_across_the_two_orders() -> None:
    result = digest_item(_RubricStub(), _brief())
    assert result.rubric == (("valuation", pytest.approx(0.5)),)
