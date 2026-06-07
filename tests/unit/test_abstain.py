"""Unit tests for abstain-when-blind.

The synthesis seat refuses to call when it is blind: an unknown trend gate, too few valid factor
states, or valid factors pointing to opposing extremes that cancel. An abstain makes no claim — the
scorer excludes it — so a calm "no read" never costs the calibration record.
"""

import pytest

from factor_scope.contract import Band, FactorState, GateState, LeanAction, ListName
from factor_scope.digest import DigestInput, digest_item
from factor_scope.digest.fake import FakeProvider

pytestmark = pytest.mark.unit


def _state(factor: str, level: Band, direction: str = "x", valid: bool = True) -> FactorState:
    return FactorState(factor=factor, level=level, direction=direction, valid=valid)


def _brief(states: list[FactorState], gate: GateState) -> DigestInput:
    return DigestInput(
        code="X", name="X", list_name=ListName.HOLDINGS, states=tuple(states), gate=gate
    )


def test_unknown_gate_abstains() -> None:
    brief = _brief(
        [
            _state("reversal", Band.LOW, "reversal-UP"),
            _state("macro dial", Band.LOW, "easy"),
        ],
        GateState.UNKNOWN,
    )
    result = digest_item(FakeProvider(), brief)
    assert result.action is LeanAction.ABSTAIN
    assert result.confidence == 0.0


def test_too_few_valid_states_abstains() -> None:
    brief = _brief(
        [
            _state("reversal", Band.HIGH, "stretched", valid=False),
            _state("macro dial", Band.HIGH, "tight", valid=False),
            _state("trend gate", Band.HIGH, "uptrend"),  # the only valid read
        ],
        GateState.OPEN,
    )
    result = digest_item(FakeProvider(), brief)
    assert result.action is LeanAction.ABSTAIN


def test_opposing_extremes_abstain() -> None:
    # A strong bull read (sold off hard) and a strong bear read (downtrend + tight) cancel.
    brief = _brief(
        [
            _state("reversal", Band.EXTREME_LOW, "sold off hard → reversal-UP potential"),
            _state("trend gate", Band.LOW, "downtrend"),
            _state("macro dial", Band.HIGH, "tight"),
        ],
        GateState.OPEN,
    )
    result = digest_item(FakeProvider(), brief)
    assert result.action is LeanAction.ABSTAIN
