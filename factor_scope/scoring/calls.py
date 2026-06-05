"""Falsifiable calls — the log the self-scoring loop scores against (spec §06).

When the digest emits a lean it becomes a :class:`Call`: a falsifiable claim — *which way*, *how
sure*, *over what horizon*, *under which state pattern*. A call is appended to the point-in-time
store (series ``calls``, keyed by ``call_id`` and stamped with the night it was made) and is then
**immutable**: the append-only store never rewrites it, so tomorrow's score sees exactly the claim
that was made. Leans land in Phase 5; Phase 4 wires the loop and feeds it a fixture of prior calls.
"""

from __future__ import annotations

from dataclasses import dataclass

from factor_scope.contract import LeanAction
from factor_scope.store import PointInTimeStore, Reading

SERIES = "calls"


@dataclass(frozen=True)
class Call:
    """One falsifiable lean: a directional claim with a confidence, horizon, and state pattern."""

    call_id: str
    code: str
    as_of: str  # the night the call was made (point-in-time)
    action: LeanAction
    confidence: float
    horizon_d: int  # the claim's resolution horizon, in calendar days
    state_pattern: tuple[str, ...] = ()  # the factor reads behind it, e.g. ("reversal:high",)
    invalidation: str | None = None  # the written flip-trigger (descriptive)

    def to_reading(self, *, fetched_at: str) -> Reading:
        return Reading(
            series=SERIES,
            key=self.call_id,
            as_of=self.as_of,
            fetched_at=fetched_at,
            payload={
                "code": self.code,
                "action": self.action.value,
                "confidence": self.confidence,
                "horizon_d": self.horizon_d,
                "state_pattern": list(self.state_pattern),
                "invalidation": self.invalidation,
            },
        )

    @classmethod
    def from_reading(cls, reading: Reading) -> Call:
        p = reading.payload
        return cls(
            call_id=reading.key,
            code=str(p["code"]),
            as_of=reading.as_of,
            action=LeanAction(p["action"]),
            confidence=float(p["confidence"]),
            horizon_d=int(p["horizon_d"]),
            state_pattern=tuple(p.get("state_pattern") or ()),
            invalidation=p.get("invalidation"),
        )


def log_call(store: PointInTimeStore, call: Call, *, fetched_at: str) -> int:
    """Append a call to the point-in-time store. Append-only — never an update (spec §09)."""

    return store.append([call.to_reading(fetched_at=fetched_at)])


def read_calls(store: PointInTimeStore, as_of: str) -> list[Call]:
    """Every call knowable on or before ``as_of`` (point-in-time), oldest first."""

    return [Call.from_reading(r) for r in store.history(SERIES) if r.as_of <= as_of]
