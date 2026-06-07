"""Regenerate the prior-calls fixture the self-scoring loop scores against.

The loop is fed a fixture of *prior* falsifiable calls
to score against the committed price history. This generator is deterministic and seedless — it
reads ``data/fixtures/prices.csv`` and emits a fixed schedule of calls (no RNG, no wall clock) — so
a fixtures run reproduces ``dashboard.json`` byte-for-byte. Run from the repo root:

    uv run python scripts/gen_calls_fixtures.py

It rewrites ``data/fixtures/calls.csv``. The calls are shaped to tell a clear calibration story:
a confident **reversal:extreme_high** "trim the winner" pattern keeps fighting an uptrend and misses
(→ surfaces as an overconfident weak pattern), while riding the **trend:open** uptrend and avoiding
the **trend:capped** downtrend are better calibrated. Outcomes are not authored — they fall out of
the real prices when the scorer runs — so the fixture stays internally consistent.
"""

from __future__ import annotations

import csv
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"

# Entry points (index into each code's oldest-first price series) and the horizon in calendar days.
# Chosen well before the series end so every call resolves on the committed prices.
_ENTRY_INDICES = (30, 70, 110, 150)
_HORIZON_D = 30

# (code, action, confidence, state_pattern) — one persona per row, repeated at each entry index.
_PERSONAS: tuple[tuple[str, str, float, str], ...] = (
    # Riding the optical-module uptrend: buy-early, calibrated.
    ("561010", "buy_early", 0.7, "trend:open"),
    # Repeatedly fighting that same uptrend with a confident "trim the winner" reversal call.
    ("561010", "trim", 0.9, "reversal:extreme_high"),
    # Avoiding the energy-storage downtrend (below its 200-day MA), confident.
    ("561160", "avoid", 0.8, "trend:capped"),
    # A flat "hold" read on the chip watch name under a tight macro dial.
    ("588200", "hold", 0.5, "macro:high|trend:open"),
)


def _load_prices() -> dict[str, list[str]]:
    """Each code's price dates, oldest first (we only need the as-of stamps to place calls)."""

    rows: dict[str, list[tuple[str, str]]] = {}
    with (FIXTURES / "prices.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows.setdefault(row["code"], []).append((row["as_of"], row["nav"]))
    return {code: [d for d, _ in sorted(obs)] for code, obs in rows.items()}


def main() -> None:
    dates = _load_prices()
    out_rows: list[dict[str, str]] = []
    seq = 0
    for code, action, confidence, pattern in _PERSONAS:
        series = dates.get(code, [])
        for idx in _ENTRY_INDICES:
            if idx >= len(series):
                continue
            seq += 1
            out_rows.append(
                {
                    "call_id": f"c{seq:03d}",
                    "code": code,
                    "as_of": series[idx],
                    "action": action,
                    "confidence": f"{confidence:.2f}",
                    "horizon_d": str(_HORIZON_D),
                    "state_pattern": pattern,
                    "invalidation": "",
                }
            )
    # One abstain to exercise the no-claim path (excluded from the score).
    abstain_series = dates.get("515880", [])
    if abstain_series:
        seq += 1
        out_rows.append(
            {
                "call_id": f"c{seq:03d}",
                "code": "515880",
                "as_of": abstain_series[_ENTRY_INDICES[0]],
                "action": "abstain",
                "confidence": "0.00",
                "horizon_d": str(_HORIZON_D),
                "state_pattern": "",
                "invalidation": "",
            }
        )

    fields = [
        "call_id",
        "code",
        "as_of",
        "action",
        "confidence",
        "horizon_d",
        "state_pattern",
        "invalidation",
    ]
    out_path = FIXTURES / "calls.csv"
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"wrote {len(out_rows)} calls to {out_path}")


if __name__ == "__main__":
    main()
