"""The synthesis seat — drive the debate, then enforce the hard guardrails.

The provider argues both sides and proposes a lean; this orchestrator owns everything that must be
true *regardless of what any model says*:

1. **Abstain when blind** — an unknown trend gate, too few valid states, or valid factors at
   opposing extremes → no claim.
2. **The trend gate is a hard rule** — a capped gate caps the lean at Hold/Avoid; nothing here may
   open it.
3. **Evidence-quality auto-downgrade** — weak evidence (stale / single-source / conflict /
   forum-only) trims the confidence *number* down before the scorecard reads it; descriptive only.
4. **The scorecard is descriptive only** — it may pull the confidence *number* toward realised
   reliability (the sole sanctioned channel), never change the action, a state, or the gate.

The descriptive fields the artifact carries (text, evolution, flip-trigger, invalidation) are then
rendered deterministically from the *final* action, so they always match the lean that ships.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date
from typing import Protocol

from factor_scope.contract import Band, GateState, LeanAction, ListName
from factor_scope.digest.fake import FLAT_EPS
from factor_scope.digest.provider import Case, DigestInput, LLMProvider, Proposal
from factor_scope.scoring.scorecard import confidence_nudge, dampen_for_weak_pattern

MIN_VALID_STATES = 2  # below this we are too blind to call
OPPOSE_MIN = 1.5  # a "strong case" floor — both sides this strong, and cancelling, → abstain
DEFAULT_HORIZON_D = 30  # the horizon a fresh lean is scored over (calendar days)

# Signed "tilt" of each lean: + bullish, − bearish, 0 inaction. Used to pick the more conservative
# of two orders (nearer inaction) and to spot a side-flip (opposite signs) the swap exposed.
_ACTION_TILT = {
    LeanAction.BUY_EARLY: 2,
    LeanAction.HOLD: 0,
    LeanAction.ABSTAIN: 0,
    LeanAction.TRIM: -1,
    LeanAction.AVOID: -1,
    LeanAction.EXIT: -2,
}

# Evidence-quality auto-downgrade — a deterministic confidence penalty on weak evidence.
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
    """The final, guardrail-checked lean for one item plus the call it will be logged as.

    ``error`` is set only when a provider seat *raised* and the item was degraded to abstain
    (mirroring ``FactorState(valid=False)``); it carries the failure for the ops run log and never
    reaches the artifact — the descriptive fields render an ordinary abstain.
    """

    action: LeanAction
    confidence: float
    text: str
    evolution: str | None
    flip_trigger: str | None
    invalidation: str | None
    state_pattern: tuple[str, ...]
    error: str | None = None
    # The debate decomposition behind the lean — the two case strengths, the position-bias residual
    # the swap-and-average removed, and the synthesis seat's rubric. The pipeline reads these into
    # the artifact's per-product index; defaults cover the no-debate (blind/error) abstains.
    bull_strength: float = 0.0
    bear_strength: float = 0.0
    order_residual: float = 0.0
    rubric: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class Debate:
    """The provider's pre-guardrail judgment for one item — the cacheable unit of the nightly cost.

    The two case strengths, the de-biased synthesis action + confidence, the position-bias residual
    the swap-and-average removed, and the synthesis rubric. Deliberately *not* the hard guardrails
    (gate, evidence-quality downgrade, scorecard): those re-run every night on the reused debate, so
    a later staleness or a moved scorecard still bites. An ``ABSTAIN`` action records the seats
    cancelling — opposing strong cases, or a side-flip the order swap exposed.
    """

    bull_strength: float
    bear_strength: float
    action: LeanAction
    confidence: float
    order_residual: float
    rubric: tuple[tuple[str, float], ...]


class DebateCache(Protocol):
    """A content-addressed store of past debates — reuse an unchanged item's judgment across nights.

    Keyed by :func:`digest_key`, so an item whose decision-relevant brief has not moved skips the
    expensive seats. Implementations own the key; the orchestrator only get/puts by brief.
    """

    def get(self, brief: DigestInput) -> Debate | None: ...

    def put(self, brief: DigestInput, debate: Debate) -> None: ...


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


def _conservative(a: LeanAction, b: LeanAction) -> LeanAction:
    """The more conservative of two orders' leans; opposite sides → abstain.

    When the two presentation orders pick *different* actions we keep the one nearer inaction; when
    they pick opposite *sides* (one bullish, one bearish) the call is too position-biased to trust,
    so it abstains. Matches the engine's allocation-over-timing / abstain-when-blind ethos.
    """

    ta, tb = _ACTION_TILT[a], _ACTION_TILT[b]
    if (ta > 0 and tb < 0) or (ta < 0 and tb > 0):
        return LeanAction.ABSTAIN
    return a if abs(ta) <= abs(tb) else b


def _average_rubric(
    fwd: tuple[tuple[str, float], ...], rev: tuple[tuple[str, float], ...]
) -> tuple[tuple[str, float], ...]:
    """Per-criterion mean of the two orders' rubrics; a criterion in only one passes through."""

    if not fwd or not rev:
        return fwd or rev
    rev_scores = dict(rev)
    merged = [
        (c, (s + rev_scores[c]) / 2.0 if c in rev_scores else s) for c, s in fwd
    ]
    seen = {c for c, _ in fwd}
    merged.extend((c, s) for c, s in rev if c not in seen)
    return tuple(merged)


def _debias(fwd: Proposal, rev: Proposal) -> tuple[Proposal, float]:
    """Average the two presentation orders into one de-biased proposal + the order residual."""

    action = fwd.action if fwd.action is rev.action else _conservative(fwd.action, rev.action)
    combined = Proposal(
        action=action,
        confidence=(fwd.confidence + rev.confidence) / 2.0,
        rubric=_average_rubric(fwd.rubric, rev.rubric),
    )
    return combined, abs(fwd.confidence - rev.confidence)


def _enforce_gate(action: LeanAction, brief: DigestInput) -> LeanAction:
    """The hard cap: below the 200-day MA, no bullish lean survives."""

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
    """Valid factor states at opposing extremes — a softer read of the abstain conflict."""

    levels = {s.level for s in brief.states if s.valid}
    return Band.EXTREME_HIGH in levels and Band.EXTREME_LOW in levels


def _is_forum_only(brief: DigestInput) -> bool:
    """Every evidence source is in the configured low-trust (retail-forum) set."""

    return bool(brief.evidence) and all(e.src in LOW_TRUST_SRC for e in brief.evidence)


def auto_downgrade(brief: DigestInput) -> float:
    """The fraction of confidence that survives the evidence-quality downgrade.

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
    brief: DigestInput,
    action: LeanAction,
    confidence: float,
    tokens: tuple[str, ...],
    *,
    bull_strength: float = 0.0,
    bear_strength: float = 0.0,
    order_residual: float = 0.0,
    rubric: tuple[tuple[str, float], ...] = (),
) -> DigestResult:
    return DigestResult(
        action=action,
        confidence=confidence,
        text=_text(action, confidence),
        evolution=_evolution(brief, action),
        flip_trigger=_flip_trigger(action, brief),
        invalidation=_invalidation(action, brief),
        state_pattern=tokens,
        bull_strength=bull_strength,
        bear_strength=bear_strength,
        order_residual=order_residual,
        rubric=rubric,
    )


def _abstain(
    brief: DigestInput,
    tokens: tuple[str, ...],
    *,
    error: str | None = None,
    bull_strength: float = 0.0,
    bear_strength: float = 0.0,
    order_residual: float = 0.0,
    rubric: tuple[tuple[str, float], ...] = (),
) -> DigestResult:
    result = _render(
        brief,
        LeanAction.ABSTAIN,
        0.0,
        tokens,
        bull_strength=bull_strength,
        bear_strength=bear_strength,
        order_residual=order_residual,
        rubric=rubric,
    )
    return result if error is None else replace(result, error=error)


def digest_key(brief: DigestInput) -> str:
    """A content hash of the *decision-relevant* brief — the cache key for cross-night debate reuse.

    Hashes what the seats actually argue over (identity, gate, valid states, evidence content,
    flagged look-through overlaps, near-misses) and deliberately *excludes* the run date, the
    evidence read-dates, the scorecard, prior action and unrealised gain. So an unchanged item on a
    new night reuses last night's judgment, while the date-sensitive guardrails (evidence staleness,
    the scorecard mirror) still re-bite on the reused debate.
    """

    payload = {
        "code": brief.code,
        "list": brief.list_name.value,
        "gate": brief.gate.value,
        "states": [
            (s.factor, s.level.value, s.direction) for s in brief.states if s.valid
        ],
        "evidence": [(e.src, e.one_line) for e in brief.evidence],
        "connections": (
            sorted(c.shared for c in brief.connections) if brief.connections_flag else []
        ),
        "near_misses": list(brief.near_misses),
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _run_debate(provider: LLMProvider, brief: DigestInput) -> Debate:
    """Argue both sides then synthesise (both orders, de-biased) into one pre-guardrail judgment.

    Raises on a provider seat failure — the caller degrades that to an abstain-with-error, so only a
    clean debate is ever returned (and cached). Two strong, cancelling cases short-circuit to an
    ABSTAIN debate without spending the synthesis seat.
    """

    bull, bear = provider.seats(brief)
    if _opposing_extremes(bull, bear):
        return Debate(bull.strength, bear.strength, LeanAction.ABSTAIN, 0.0, 0.0, ())
    # Argue the synthesis in both seat orders and average — the swap-and-average de-bias that kills
    # the model's position bias (the fake is order-invariant, so offline this is a no-op).
    fwd = provider.synthesize(brief, bull, bear, present_bear_first=False)
    rev = provider.synthesize(brief, bull, bear, present_bear_first=True)
    proposal, residual = _debias(fwd, rev)
    return Debate(
        bull.strength,
        bear.strength,
        proposal.action,
        proposal.confidence,
        residual,
        proposal.rubric,
    )


def _apply_guardrails(brief: DigestInput, debate: Debate, tokens: tuple[str, ...]) -> DigestResult:
    """Enforce the hard guardrails on a (possibly reused) debate → the final lean.

    The gate caps a bullish lean, an ABSTAIN debate stays an abstain, and on a real action the
    evidence-quality downgrade then the scorecard mirror pull the confidence *number* — both
    descriptive and judged against the brief's own ``as_of``, so a debate reused on a new night
    still re-bites on a later staleness or a moved scorecard.
    """

    action = _enforce_gate(debate.action, brief)
    if action is LeanAction.ABSTAIN:  # the seats cancelled — no trustworthy claim
        return _abstain(
            brief,
            tokens,
            bull_strength=debate.bull_strength,
            bear_strength=debate.bear_strength,
            order_residual=debate.order_residual,
            rubric=debate.rubric,
        )
    # Confidence channels, in order: the evidence-quality downgrade trims the *stated* confidence
    # for weak evidence first, then the scorecard mirror pulls that toward realised reliability.
    # Both are descriptive — neither can change the action or the gate.
    downgraded = debate.confidence * auto_downgrade(brief)
    confidence = _apply_scorecard(downgraded, brief, tokens)
    return _render(
        brief,
        action,
        confidence,
        tokens,
        bull_strength=debate.bull_strength,
        bear_strength=debate.bear_strength,
        order_residual=debate.order_residual,
        rubric=debate.rubric,
    )


class SeatBudget:
    """A per-night ceiling on *fresh* seat calls — the bull/bear seats are the nightly cost.

    A slot is claimed only when an item is about to actually argue (a cache miss on a seeing brief);
    a reused or blind item costs nothing and never spends one. Once the night's slots are gone the
    remaining lower-priority items degrade to an abstain-with-error, the ceiling living outside the
    model exactly like the trend gate.
    """

    def __init__(self, limit: int) -> None:
        self._remaining = limit

    def claim(self) -> bool:
        """Reserve one fresh seat call; ``False`` once the night's ceiling is spent."""

        if self._remaining <= 0:
            return False
        self._remaining -= 1
        return True


def digest_item(
    provider: LLMProvider,
    brief: DigestInput,
    *,
    cache: DebateCache | None = None,
    budget: SeatBudget | None = None,
) -> DigestResult:
    """Run the debate and emit one item's guardrail-checked lean (the synthesis seat).

    A seat that *raises* (a missing/failed/slow ``claude`` call, malformed model JSON) degrades this
    item to an abstain carrying the error — the run completes on a sparser artifact rather than the
    night aborting. This is the same "invalid degrades, never raises" invariant the factor battery
    holds, enforced here at the provider boundary.

    With a ``cache``, an item whose decision-relevant brief is unchanged (same :func:`digest_key`)
    reuses a prior night's debate instead of re-arguing the seats — the nightly cost — while the
    hard guardrails still re-run on it. A ``budget`` caps how many *fresh* debates the night argues:
    only a cache miss on a seeing item claims a slot, and once they are spent the item
    abstains-with-error instead of arguing. Blind and reused items spend nothing.
    """

    tokens = state_tokens(brief)
    if _blind_reason(brief) is not None:
        return _abstain(brief, tokens)

    debate = cache.get(brief) if cache is not None else None
    if debate is None:
        if budget is not None and not budget.claim():
            return _abstain(brief, tokens, error="seat budget exhausted")
        try:
            debate = _run_debate(provider, brief)
        except Exception as exc:  # noqa: BLE001 - any seat failure degrades; never abort the run
            return _abstain(brief, tokens, error=f"{type(exc).__name__}: {exc}")
        if cache is not None:
            cache.put(brief, debate)

    return _apply_guardrails(brief, debate, tokens)


__all__ = [
    "DEFAULT_HORIZON_D",
    "LOW_TRUST_SRC",
    "MIN_SOURCES",
    "STALE_MAX_AGE_D",
    "Debate",
    "DebateCache",
    "DigestResult",
    "SeatBudget",
    "auto_downgrade",
    "digest_item",
    "digest_key",
    "state_tokens",
]
