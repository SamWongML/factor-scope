"""Calls adapter — prior falsifiable leans the self-scoring loop scores against.

Real leans come from the digest; the loop is also seeded with a fixture of prior calls so
the scorecard is real at every boundary. Each row is a :class:`~factor_scope.scoring.calls.Call`
stamped with the night it was made (its own ``as_of``), so reads stay point-in-time. The
``state_pattern`` column is a ``|``-separated list of factor reads, e.g.
``trend:capped|reversal:extreme_high``.
"""

from __future__ import annotations

from pathlib import Path

from factor_scope.contract import LeanAction
from factor_scope.ingest.base import IngestError, as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "calls"
FIXTURE = "calls.csv"
_REQUIRED = ("call_id", "code", "as_of", "action", "confidence", "horizon_d", "state_pattern")


def parse(text: str, *, fetched_at: str) -> list[Reading]:
    """Parse a `calls.csv` body into readings stamped with each call's own ``as_of``."""

    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        call_id = required_str(row, "call_id", line_no, SERIES)
        code = required_str(row, "code", line_no, SERIES)
        as_of = required_str(row, "as_of", line_no, SERIES)
        raw_action = required_str(row, "action", line_no, SERIES)
        try:
            action = LeanAction(raw_action)
        except ValueError as exc:
            raise IngestError(
                f"{SERIES} line {line_no}: action={raw_action!r} is not one of "
                f"{[m.value for m in LeanAction]}"
            ) from exc
        confidence = as_float(row, "confidence", line_no, SERIES)
        horizon_d = int(as_float(row, "horizon_d", line_no, SERIES))
        pattern_raw = (row.get("state_pattern") or "").strip()
        state_pattern = [tok for tok in pattern_raw.split("|") if tok]
        invalidation = (row.get("invalidation") or "").strip() or None
        readings.append(
            Reading(
                series=SERIES,
                key=call_id,
                as_of=as_of,
                fetched_at=fetched_at,
                payload={
                    "code": code,
                    "action": action.value,
                    "confidence": confidence,
                    "horizon_d": horizon_d,
                    "state_pattern": state_pattern,
                    "invalidation": invalidation,
                },
            )
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)
