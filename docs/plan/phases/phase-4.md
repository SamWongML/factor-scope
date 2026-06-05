# Phase 4 — Self-scoring loop (L3)  ·  STATUS: done

Spec: §06. Principle: the scorecard is **descriptive only**.

## Goal
Log each lean as a falsifiable call, score it mechanically the next day, and surface a rolling
`scorecard` to the digest as a read-only mirror.

## Design
- `factor_scope/scoring/`:
  - `calls.py` — append a call `{call_id, item, as_of, lean, confidence, horizon_d, state_pattern[]}`
    to the point-in-time store when the digest emits a lean. Immutable once resolved.
  - `scorer.py` — next-day mechanical scoring: forward return over the horizon vs stated direction →
    `outcome ∈ {hit, miss, abstain}`, `fwd_ret`. **No LLM, no memory, no opinion.**
  - `scorecard.py` — pure functions over `(confidence, outcome)` pairs: **Brier** score, **Brier skill
    score** vs base rate, reliability-by-confidence buckets, per-state-pattern hit-rate. Gated on a
    minimum `n` and a rolling window.
- Guardrails in code: the scorecard may set notes / nudge confidence inputs, but cannot mutate a
  `FactorState`, change `gate`, or supply a number to the artifact. Add a test that this is impossible.

## TDD plan
- `tests/unit/test_brier.py`: Brier + skill score on hand-computed cases (perfect/worst/base-rate).
- `tests/unit/test_reliability.py`: bucketing + realised hit-rate; min-sample gating hides noise.
- `tests/unit/test_scorer.py`: forward-return labelling; abstain handling; immutability of a resolved call.
- `tests/unit/test_guardrails.py`: scorecard cannot open a capped gate or change a state.

## System test
Seeded calls + outcomes → expected `scorecard` block attached to items; artifact valid + deterministic.

## Done when
Scoring math verified; guardrails enforced; `make system` green; `PROGRESS.md` + commit.
