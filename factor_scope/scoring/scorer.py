"""The mechanical next-day scorer.

Scoring a call is pure mechanics, never judgment: take the realised forward return over the call's
horizon and compare its sign to the lean's stated direction → ``hit`` / ``miss`` / ``abstain``.
**No LLM, no memory, no opinion.** It is point-in-time on both sides — the entry price is the one
in force on the call date and the exit is the one in force at the resolution date — so a settled
call is immutable: prices that arrive after the window can never move a score that has resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from factor_scope.contract import LeanAction
from factor_scope.scoring.calls import Call, read_calls
from factor_scope.store import PointInTimeStore

# A small dead-band: moves inside it are "flat". A directional lean must clear it to be a hit; a
# Hold (flat) lean is a hit only while the tape stays inside it.
FLAT_BAND = 0.005


class Outcome(StrEnum):
    """The mechanical verdict on a resolved call."""

    HIT = "hit"
    MISS = "miss"
    ABSTAIN = "abstain"  # the lean made no claim — excluded from the score


@dataclass(frozen=True)
class ScoredCall:
    """A call plus its settled, immutable outcome."""

    call: Call
    outcome: Outcome
    fwd_ret: float | None  # the realised forward return; None for an abstain
    resolved_on: str  # the price date the horizon resolved against


# Which way a lean points: +1 up, -1 down, 0 flat (Hold), None = no claim (Abstain).
_DIRECTION: dict[LeanAction, int | None] = {
    LeanAction.BUY_EARLY: 1,
    LeanAction.HOLD: 0,
    LeanAction.TRIM: -1,
    LeanAction.EXIT: -1,
    LeanAction.AVOID: -1,
    LeanAction.ABSTAIN: None,
}


def lean_direction(action: LeanAction) -> int | None:
    """The signed direction a lean claims, or ``None`` when it abstains."""

    return _DIRECTION[action]


def classify_outcome(direction: int, fwd_ret: float, flat_band: float = FLAT_BAND) -> Outcome:
    """Hit/miss for a directional (``±1``) or flat (``0``) claim vs the realised return."""

    if direction == 0:  # Hold predicted a flat tape
        return Outcome.HIT if abs(fwd_ret) <= flat_band else Outcome.MISS
    if direction > 0:
        return Outcome.HIT if fwd_ret > flat_band else Outcome.MISS
    return Outcome.HIT if fwd_ret < -flat_band else Outcome.MISS


def _forward_return(
    store: PointInTimeStore, code: str, as_of: str, horizon_d: int, run_as_of: str
) -> tuple[str, float] | None:
    """The point-in-time forward return over the horizon, or ``None`` if not yet resolvable.

    Entry is the latest price in force on the call date; exit is the latest price in force on the
    resolution date (``as_of + horizon_d`` days). Returns ``None`` until that resolution date has
    arrived (``<= run_as_of``) and a later price exists to mark against.
    """

    target = (date.fromisoformat(as_of) + timedelta(days=horizon_d)).isoformat()
    if target > run_as_of:
        return None  # the horizon has not elapsed yet — still pending
    rows = sorted(
        (r for r in store.history("prices", code) if r.as_of <= run_as_of),
        key=lambda r: (r.as_of, r.fetched_at),
    )
    entry = next((r for r in reversed(rows) if r.as_of <= as_of), None)
    exit_ = next((r for r in reversed(rows) if r.as_of <= target), None)
    if entry is None or exit_ is None or exit_.as_of <= entry.as_of:
        return None  # no entry mark, or no forward price past the entry
    entry_nav = float(entry.payload["nav"])
    exit_nav = float(exit_.payload["nav"])
    if entry_nav == 0:
        return None
    return exit_.as_of, exit_nav / entry_nav - 1.0


def window_open(store: PointInTimeStore, code: str, as_of: str) -> bool:
    """True once a price dated after ``as_of`` exists for ``code`` — the forward-return window has
    opened, so the call's outcome is starting to become knowable.

    The seal on committing a *new* call: a directional lean dated ``as_of`` is falsifiable only
    while its forward return is not yet observable, so it may be committed only before this line. A
    genuine call made within the window is already immutable; only a stale re-run (the store now
    holding prices past ``as_of``) reaches here, and back-filling a call once the move is knowable
    would be hindsight. In a forward-moving nightly the store never holds a price past the run date,
    so this is ``False`` in normal operation and bites only the stale back-fill.
    """

    return any(r.as_of > as_of for r in store.history("prices", code))


def score_call(
    call: Call, store: PointInTimeStore, run_as_of: str, *, flat_band: float = FLAT_BAND
) -> ScoredCall | None:
    """Score one call as of ``run_as_of``; ``None`` while it is still pending."""

    direction = lean_direction(call.action)
    if direction is None:  # an abstain settles immediately and makes no claim
        return ScoredCall(call=call, outcome=Outcome.ABSTAIN, fwd_ret=None, resolved_on=call.as_of)
    resolved = _forward_return(store, call.code, call.as_of, call.horizon_d, run_as_of)
    if resolved is None:
        return None
    resolved_on, fwd_ret = resolved
    return ScoredCall(
        call=call,
        outcome=classify_outcome(direction, fwd_ret, flat_band),
        fwd_ret=fwd_ret,
        resolved_on=resolved_on,
    )


def score_calls(
    store: PointInTimeStore, run_as_of: str, *, flat_band: float = FLAT_BAND
) -> list[ScoredCall]:
    """Score every knowable call; pending ones are dropped, settled ones kept (oldest first)."""

    scored: list[ScoredCall] = []
    for call in read_calls(store, run_as_of):
        result = score_call(call, store, run_as_of, flat_band=flat_band)
        if result is not None:
            scored.append(result)
    return scored
