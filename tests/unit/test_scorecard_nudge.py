"""Unit tests for the scorecard's confidence channel into the digest (spec §06/§08).

The self-scoring mirror's *only* influence on tomorrow's lean is the confidence number: it pulls a
stated confidence toward the bucket's realised reliability and dampens it on a state-pattern it has
been systematically overconfident on. It can never change the action, the states, or the gate.
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
from factor_scope.digest import DigestInput, digest_item
from factor_scope.digest.fake import FakeProvider
from factor_scope.scoring.scorecard import dampen_for_weak_pattern

pytestmark = pytest.mark.unit


def _state(factor: str, level: Band, direction: str = "x") -> FactorState:
    return FactorState(factor=factor, level=level, direction=direction, valid=True)


def _bearish_brief(scorecard: Scorecard | None) -> DigestInput:
    # A holding stretched up (the reversal:extreme_high pattern) under a tight macro dial → Trim.
    return DigestInput(
        code="X",
        name="X",
        list_name=ListName.HOLDINGS,
        states=(
            _state("reversal", Band.EXTREME_HIGH, "ran up hard → reversal-DOWN risk"),
            _state("trend gate", Band.HIGH, "uptrend"),
            _state("macro dial", Band.HIGH, "tight"),
        ),
        gate=GateState.OPEN,
        scorecard=scorecard,
    )


def test_overconfident_pattern_lowers_confidence() -> None:
    card = Scorecard(
        window="60d",
        n=20,
        reliability=[ReliabilityBucket(bucket=0.6, realised=0.6)],
        weak_patterns=["reversal:extreme_high overconfident (hit 0% vs conf 90%, n=4)"],
    )
    without = digest_item(FakeProvider(), _bearish_brief(None))
    with_card = digest_item(FakeProvider(), _bearish_brief(card))
    assert with_card.action is without.action is LeanAction.TRIM  # the action is untouched
    assert with_card.confidence < without.confidence  # only the confidence is dampened


def test_dampen_is_identity_without_a_matching_pattern() -> None:
    card = Scorecard(window="60d", n=20, weak_patterns=["macro:high+trend:open overconfident"])
    assert dampen_for_weak_pattern(card, 0.8, ("reversal:low", "trend:open")) == 0.8


def test_dampen_only_lowers_confidence() -> None:
    card = Scorecard(window="60d", n=20, weak_patterns=["reversal:extreme_high overconfident"])
    out = dampen_for_weak_pattern(card, 0.8, ("reversal:extreme_high",))
    assert 0.0 <= out < 0.8


def test_dampen_is_identity_without_a_scorecard() -> None:
    assert dampen_for_weak_pattern(None, 0.7, ("reversal:extreme_high",)) == 0.7
