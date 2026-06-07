"""The synthesis seat — drive the debate, then enforce the hard guardrails.

The provider argues both sides and proposes a lean; this orchestrator owns everything that must be
true *regardless of what any model says*:

1. **Abstain when blind** — an unknown trend gate, too few valid states, or valid factors at
   opposing extremes → no claim.
2. **The trend gate is a hard rule** — a capped gate caps the lean at Hold/Avoid; nothing here may
   open it (principle #4).
3. **Evidence-quality auto-downgrade** — weak evidence (stale / single-source / conflict /
   forum-only) trims the confidence *number* down before the scorecard reads it; descriptive only.
4. **The scorecard is descriptive only** — it may pull the confidence *number* toward realised
   reliability (the sole sanctioned channel), never change the action, a state, or the gate.

The descriptive fields the artifact carries (text, evolution, flip-trigger, invalidation) are then
rendered deterministically from the *final* action, so they always match the lean that ships.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from factor_scope.contract import Band, GateState, LeanAction, ListName
from factor_scope.digest.fake import FLAT_EPS
from factor_scope.digest.provider import Case, DigestInput, LLMProvider, Side
from factor_scope.scoring.scorecard import confidence_nudge, dampen_for_weak_pattern

MIN_VALID_STATES = 2  # below this we are too blind to call
OPPOSE_MIN = 1.5  # a "strong case" floor — both sides this strong, and cancelling, → abstain
DEFAULT_HORIZON_D = 30  # the horizon a fresh lean is scored over (calendar days)

# Evidence-quality auto-downgrade (spec §08) — a deterministic confidence penalty on weak evidence.
STALE_MAX_AGE_D = 7  # newest evidence older than this (vs the brief's as_of) reads as stale
MIN_SOURCES = 2  # fewer than this many distinct evidence sources reads as single-source
LOW_TRUST_SRC = frozenset({"xueqiu", "guba", "tieba"})  # retail forums → forum-only when all match

# How much confidence *survives* each condition (a fraction in (0, 1]); they multiply, so several
# weak signals compound. These are fixed, never tuned to returns.
_STALE_KEEP = 0.85
_SINGLE_SOURCE_KEEP = 0.9
_CONFLICT_KEEP = 0.85
_FORUM_ONLY_KEEP = 0.8

# Short, stable factor tokens for the state-pattern key the scorecard reasons over. The trend factor
# is represented by the gate token, so it is skipped here to avoid a double read.
_SHORT_FACTOR = {
    "reversal": "reversal",
    "macro dial": "macro",
    "low-vol/drawdown": "lowvol",
    "crowding": "crowding",
    "demand": "demand",
    "valuation": "valuation",
    "cross-market lead": "lead",
}

_ACTION_LABEL = {
    LeanAction.BUY_EARLY: "Buy-early",
    LeanAction.HOLD: "Hold",
    LeanAction.TRIM: "Trim",
    LeanAction.EXIT: "Exit",
    LeanAction.AVOID: "Avoid",
    LeanAction.ABSTAIN: "Abstain",
}

_BULLISH = {LeanAction.BUY_EARLY}


@dataclass(frozen=True)
class DigestResult:
    """The final, guardrail-checked lean for one item plus the call it will be logged as."""

    action: LeanAction
    confidence: float
    text: str
    evolution: str | None
    flip_trigger: str | None
    invalidation: str | None
    state_pattern: tuple[str, ...]


def state_tokens(brief: DigestInput) -> tuple[str, ...]:
    """The stable state-pattern key for this item — what the scorecard's weak-pattern read uses."""

    tokens: list[str] = []
    if brief.gate is GateState.OPEN:
        tokens.append("trend:open")
    elif brief.gate is GateState.CAPPED:
        tokens.append("trend:capped")
    for state in brief.states:
        if not state.valid or state.level is Band.NEUTRAL or state.factor == "trend gate":
            continue
        short = _SHORT_FACTOR.get(state.factor)
        if short is not None:
            tokens.append(f"{short}:{state.level.value}")
    return tuple(tokens)


def _blind_reason(brief: DigestInput) -> str | None:
    """Why the synthesis seat must abstain, or ``None`` if it can see well enough to call."""

    if brief.gate is GateState.UNKNOWN:
        return "trend gate unknown (too little history to judge the trend)"
    if sum(1 for s in brief.states if s.valid) < MIN_VALID_STATES:
        return "too few valid factor states to call"
    return None


def _opposing_extremes(bull: Case, bear: Case) -> bool:
    """Both sides marshalled a strong case and they cancel → no actionable read (abstain)."""

    return (
        bull.strength >= OPPOSE_MIN
        and bear.strength >= OPPOSE_MIN
        and abs(bull.strength - bear.strength) <= FLAT_EPS
    )


def _enforce_gate(action: LeanAction, brief: DigestInput) -> LeanAction:
    """The hard cap: below the 200-day MA, no bullish lean survives (principle #4)."""

    if brief.gate is GateState.CAPPED and action in _BULLISH:
        return LeanAction.HOLD if brief.list_name is ListName.HOLDINGS else LeanAction.AVOID
    return action


def _is_stale(brief: DigestInput) -> bool:
    """Newest evidence older than the freshness window, measured against the brief's own as_of."""

    if brief.as_of is None or not brief.evidence:
        return False
    newest = max(date.fromisoformat(e.as_of) for e in brief.evidence)
    return (date.fromisoformat(brief.as_of) - newest).days > STALE_MAX_AGE_D


def _is_single_source(brief: DigestInput) -> bool:
    """Fewer than ``MIN_SOURCES`` distinct sources (no evidence counts as single-source)."""

    return len({e.src for e in brief.evidence}) < MIN_SOURCES


def _is_conflict(brief: DigestInput) -> bool:
    """Valid factor states at opposing extremes — a softer read of the abstain conflict (§08)."""

    levels = {s.level for s in brief.states if s.valid}
    return Band.EXTREME_HIGH in levels and Band.EXTREME_LOW in levels


def _is_forum_only(brief: DigestInput) -> bool:
    """Every evidence source is in the configured low-trust (retail-forum) set."""

    return bool(brief.evidence) and all(e.src in LOW_TRUST_SRC for e in brief.evidence)


def auto_downgrade(brief: DigestInput) -> float:
    """The fraction of confidence that survives the evidence-quality downgrade (spec §08).

    A deterministic, pure function of the brief — no wall clock (staleness is judged against the
    brief's own ``as_of``). Each weak-evidence condition (stale / single-source / conflict /
    forum-only) multiplies the survivor toward zero, so several signals compound. The result is in
    ``(0, 1]``: it can only *lower* confidence, never raise it, and it never touches the action, a
    state, or the gate — the caller multiplies the stated confidence by it.
    """

    keep = 1.0
    if _is_stale(brief):
        keep *= _STALE_KEEP
    if _is_single_source(brief):
        keep *= _SINGLE_SOURCE_KEEP
    if _is_conflict(brief):
        keep *= _CONFLICT_KEEP
    if _is_forum_only(brief):
        keep *= _FORUM_ONLY_KEEP
    return keep


def _apply_scorecard(confidence: float, brief: DigestInput, tokens: tuple[str, ...]) -> float:
    """Pull the stated confidence toward realised reliability — the mirror's only channel."""

    nudged = confidence_nudge(brief.scorecard, confidence)
    dampened = dampen_for_weak_pattern(brief.scorecard, nudged, tokens)
    return round(dampened, 4)


def _conviction(confidence: float) -> str:
    if confidence < 0.55:
        return "low-conviction"
    if confidence < 0.7:
        return "moderate-conviction"
    return "high-conviction"


def _text(action: LeanAction, confidence: float) -> str:
    if action is LeanAction.ABSTAIN:
        return "Abstain — too blind to call"
    return f"{_ACTION_LABEL[action]} / {_conviction(confidence)}"


def _evolution(brief: DigestInput, action: LeanAction) -> str:
    """How the lean moved from the last call on this code (descriptive, point-in-time)."""

    label = _ACTION_LABEL[action]
    prior = brief.prior_action
    if prior is None:
        return f"new → {label}"
    if prior is action:
        return f"{label} (steady)"
    return f"{_ACTION_LABEL[prior]}→{label}"


def _flip_trigger(action: LeanAction, brief: DigestInput) -> str:
    if action is LeanAction.ABSTAIN:
        return "a known trend gate and ≥2 valid factor reads"
    if brief.gate is GateState.CAPPED:
        return "a sustained reclaim of the 200-day MA reopens the gate"
    if action in (LeanAction.TRIM, LeanAction.EXIT):
        return "the reversal stretch resets (a pullback completes) and breadth turns back up"
    if action is LeanAction.BUY_EARLY:
        return "a close below the 200-day MA would cap the lean"
    if action is LeanAction.AVOID:
        return "a reclaim of the 200-day MA together with an easing macro dial"
    return "a decisive break beyond the recent range, either side"


def _invalidation(action: LeanAction, brief: DigestInput) -> str | None:
    if action is LeanAction.ABSTAIN:
        return None  # an abstain makes no falsifiable claim
    if action in (LeanAction.TRIM, LeanAction.EXIT, LeanAction.AVOID):
        return "NAV makes a new high on rising breadth (the down-risk did not play out)"
    if action is LeanAction.BUY_EARLY:
        return "NAV rolls over below the 200-day MA within the horizon"
    return "NAV breaks decisively out of its range within the horizon"


def _render(
    brief: DigestInput, action: LeanAction, confidence: float, tokens: tuple[str, ...]
) -> DigestResult:
    return DigestResult(
        action=action,
        confidence=confidence,
        text=_text(action, confidence),
        evolution=_evolution(brief, action),
        flip_trigger=_flip_trigger(action, brief),
        invalidation=_invalidation(action, brief),
        state_pattern=tokens,
    )


def _abstain(brief: DigestInput, tokens: tuple[str, ...]) -> DigestResult:
    return _render(brief, LeanAction.ABSTAIN, 0.0, tokens)


def digest_item(provider: LLMProvider, brief: DigestInput) -> DigestResult:
    """Run the debate and emit one item's guardrail-checked lean (the synthesis seat)."""

    tokens = state_tokens(brief)
    if _blind_reason(brief) is not None:
        return _abstain(brief, tokens)

    bull = provider.argue(Side.BULL, brief)
    bear = provider.argue(Side.BEAR, brief)
    if _opposing_extremes(bull, bear):
        return _abstain(brief, tokens)

    proposal = provider.synthesize(brief, bull, bear)
    action = _enforce_gate(proposal.action, brief)
    # Confidence channels, in order: the evidence-quality downgrade (§08) trims the *stated*
    # confidence for weak evidence first, then the scorecard mirror (§06) pulls that toward
    # realised reliability. Both are descriptive — neither can change the action or the gate.
    downgraded = proposal.confidence * auto_downgrade(brief)
    confidence = _apply_scorecard(downgraded, brief, tokens)
    return _render(brief, action, confidence, tokens)


__all__ = [
    "DEFAULT_HORIZON_D",
    "LOW_TRUST_SRC",
    "MIN_SOURCES",
    "STALE_MAX_AGE_D",
    "DigestResult",
    "auto_downgrade",
    "digest_item",
    "state_tokens",
]
