"""The deterministic fake digestion provider (default).

Pure rules over the factor states — **no network, no keys, no RNG, no wall clock** — so the whole
bull/bear→synthesis pipeline runs end-to-end in CI and a fixtures run reproduces ``dashboard.json``
byte-for-byte. Each valid, non-neutral state casts a signed *risk* vote (in A-shares a stretched
up-move is reversal-DOWN risk, so it votes bearish). The bull seat marshals the positive votes, the
bear seat the negative ones (consider-the-opposite over the same facts), and the synthesis seat nets
them into a lean and anchors a base-rate confidence. **This is not a fitted composite** — the votes
are fixed economic signs, never tuned to returns, and the hard gate/abstain/scorecard guardrails are
enforced by the orchestrator, not here.
"""

from __future__ import annotations

from factor_scope.contract import Band, FactorState, LeanAction, ListName
from factor_scope.digest.provider import Case, DigestInput, Proposal, Side

# Net-vote thresholds (constants, not tuned): a |net| this large is a strong, conviction-worthy
# read; the dead-band below FLAT_EPS is "no actionable tilt" → Hold / keep watching.
STRONG = 2.0
FLAT_EPS = 0.5

# Confidence is anchored on a coin-flip base rate and widened only modestly by the net read.
BASE_CONFIDENCE = 0.5
CONFIDENCE_SLOPE = 0.1
MAX_CONFIDENCE = 0.85

# Per-band risk votes for the data-backed factors. Positive = bullish for the security; negative =
# bearish. The reversal sign is the A-share tell: a stretched up-move is reversal-DOWN risk.
_REVERSAL_VOTE = {
    Band.EXTREME_HIGH: -2.0,
    Band.HIGH: -1.0,
    Band.NEUTRAL: 0.0,
    Band.LOW: 1.0,
    Band.EXTREME_LOW: 2.0,
}
_MACRO_VOTE = {
    Band.EXTREME_HIGH: -1.0,  # tight: real-yield high → liquidity headwind
    Band.HIGH: -0.5,
    Band.NEUTRAL: 0.0,
    Band.LOW: 0.5,
    Band.EXTREME_LOW: 1.0,  # easy: liquidity tailwind
}
_LOWVOL_VOTE = {
    Band.EXTREME_HIGH: -1.0,  # stressed
    Band.HIGH: -0.5,
    Band.NEUTRAL: 0.0,
    Band.LOW: 0.5,  # calm regime
    Band.EXTREME_LOW: 0.5,
}


def _state_vote(state: FactorState) -> tuple[float, str] | None:
    """One state's signed risk vote and a human label, or ``None`` if it does not vote."""

    if not state.valid or state.level is Band.NEUTRAL:
        return None
    if state.factor == "reversal":
        return _REVERSAL_VOTE[state.level], f"reversal {state.level.value} ({state.direction})"
    if state.factor == "macro dial":
        return _MACRO_VOTE[state.level], f"macro {state.level.value} ({state.direction})"
    if state.factor == "low-vol/drawdown":
        return _LOWVOL_VOTE[state.level], f"low-vol {state.level.value} ({state.direction})"
    if state.factor == "trend gate":
        # The trend factor reads its own direction; the *hard* gate is enforced separately.
        if "uptrend" in state.direction:
            return 1.0, f"trend uptrend ({state.direction})"
        if "downtrend" in state.direction:
            return -1.0, f"trend downtrend ({state.direction})"
    return None


def _votes(brief: DigestInput) -> list[tuple[float, str]]:
    return [v for v in (_state_vote(s) for s in brief.states) if v is not None]


class FakeProvider:
    """A deterministic rules provider — the default :class:`LLMProvider` (no network, no RNG)."""

    name = "fake"

    def argue(self, side: Side, brief: DigestInput) -> Case:
        """Marshal one side's supporting reads from the shared brief (consider-the-opposite)."""

        votes = _votes(brief)
        if side is Side.BULL:
            supporting = [(v, label) for v, label in votes if v > 0]
        else:
            supporting = [(-v, label) for v, label in votes if v < 0]
        strength = sum(v for v, _ in supporting)
        confidence = min(MAX_CONFIDENCE, BASE_CONFIDENCE + CONFIDENCE_SLOPE * strength)
        return Case(
            side=side,
            strength=strength,
            confidence=confidence,
            points=tuple(label for _, label in supporting),
        )

    def synthesize(self, brief: DigestInput, bull: Case, bear: Case) -> Proposal:
        """Net the two cases into a lean + a base-rate-anchored confidence (no guardrails here)."""

        net = bull.strength - bear.strength
        owned = brief.list_name is ListName.HOLDINGS
        action = _net_to_action(net, owned)
        confidence = min(MAX_CONFIDENCE, BASE_CONFIDENCE + CONFIDENCE_SLOPE * abs(net))
        rationale = (f"bull {bull.strength:g} vs bear {bear.strength:g} → net {net:+g}",)
        return Proposal(action=action, confidence=confidence, rationale=rationale)


def _net_to_action(net: float, owned: bool) -> LeanAction:
    """Map a net read to a lean — biased toward inaction (most mornings, do nothing)."""

    if owned:
        if net <= -STRONG:
            return LeanAction.EXIT
        if net <= -FLAT_EPS:
            return LeanAction.TRIM
        return LeanAction.HOLD
    if net >= STRONG:
        return LeanAction.BUY_EARLY
    if net <= -FLAT_EPS:
        return LeanAction.AVOID
    return LeanAction.HOLD


__all__ = ["FakeProvider", "STRONG", "FLAT_EPS"]
