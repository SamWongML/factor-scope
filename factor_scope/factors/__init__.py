"""Factor states + the trend gate (L3 core, spec §03).

Each raw input becomes a descriptive *state* — a band vs its own history, a ``direction``, and a
``valid`` flag — never a fitted weight and never a composite. The public surface is the battery
(``compute_states``), the hard 200-day trend gate (``compute_gate``), and the per-item read
context (:class:`FactorContext`). Individual factor functions live in :mod:`.battery`.
"""

from __future__ import annotations

from factor_scope.factors.battery import (
    FACTOR_NAMES,
    FACTORS,
    FactorContext,
    compute_gate,
    compute_states,
)

__all__ = [
    "FACTORS",
    "FACTOR_NAMES",
    "FactorContext",
    "compute_gate",
    "compute_states",
]
