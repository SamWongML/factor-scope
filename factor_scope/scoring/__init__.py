"""Self-scoring loop (L3, spec §06) — the judgment layer's durability mechanism.

Each lean is logged as a falsifiable :class:`~factor_scope.scoring.calls.Call`, scored next day by
the mechanical :mod:`~factor_scope.scoring.scorer` (forward return vs stated direction), and rolled
up into a descriptive :class:`~factor_scope.contract.Scorecard` by
:func:`~factor_scope.scoring.scorecard.build_scorecard`. The scorecard is a mirror only — it never
changes a state, opens the gate, or supplies a number to the artifact.
"""

from __future__ import annotations

from factor_scope.scoring.calls import Call, log_call, read_calls
from factor_scope.scoring.scorecard import (
    build_scorecard,
    confidence_nudge,
)
from factor_scope.scoring.scorer import (
    Outcome,
    ScoredCall,
    classify_outcome,
    lean_direction,
    score_call,
    score_calls,
)

__all__ = [
    "Call",
    "Outcome",
    "ScoredCall",
    "build_scorecard",
    "classify_outcome",
    "confidence_nudge",
    "lean_direction",
    "log_call",
    "read_calls",
    "score_call",
    "score_calls",
]
