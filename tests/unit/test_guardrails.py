"""Unit tests for the scorecard guardrails (descriptive only).

The scorecard is a mirror. It may nudge a stated confidence (a number it does not own) within
bounds, but it can never mutate a factor state, change the trend gate, or supply a number to the
artifact. These tests make those violations structurally impossible.
"""

import pytest
from pydantic import ValidationError

from factor_scope.contract import (
    Band,
    FactorState,
    GateState,
    ReliabilityBucket,
    Scorecard,
)
from factor_scope.scoring.scorecard import confidence_nudge

pytestmark = pytest.mark.unit


def test_factor_state_is_immutable() -> None:
    state = FactorState(factor="trend gate", level=Band.LOW, direction="downtrend")
    with pytest.raises(ValidationError):
        state.level = Band.EXTREME_HIGH  # type: ignore[misc]


def test_scorecard_cannot_carry_a_state_or_a_gate() -> None:
    # The mirror's surface is purely descriptive: no field is a FactorState or a GateState,
    # so it cannot smuggle a state or open the gate via the contract.
    annotations = {name: f.annotation for name, f in Scorecard.model_fields.items()}
    assert FactorState not in annotations.values()
    assert GateState not in annotations.values()


def test_confidence_nudge_stays_within_bounds() -> None:
    # Even a degenerate mirror cannot push confidence outside [0, 1].
    card = Scorecard(
        window="60d",
        n=20,
        reliability=[ReliabilityBucket(bucket=0.9, realised=0.0, note="overconfident")],
    )
    for base in (0.0, 0.5, 0.9, 1.0):
        nudged = confidence_nudge(card, base)
        assert 0.0 <= nudged <= 1.0


def test_confidence_nudge_pulls_toward_realised_reliability() -> None:
    card = Scorecard(
        window="60d",
        n=20,
        reliability=[ReliabilityBucket(bucket=0.9, realised=0.5, note="overconfident")],
    )
    # an overconfident 0.9 bucket pulls a stated 0.9 down toward its realised 0.5
    assert confidence_nudge(card, 0.9) < 0.9


def test_confidence_nudge_is_identity_without_a_matching_bucket() -> None:
    card = Scorecard(window="60d", n=0)  # gated, empty mirror
    assert confidence_nudge(card, 0.7) == 0.7
    assert confidence_nudge(None, 0.7) == 0.7
