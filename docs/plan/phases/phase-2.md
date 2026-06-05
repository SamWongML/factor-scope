# Phase 2 — Factor states + trend gate (L3 core)  ·  STATUS: done

Spec: §03. Principle: **states, not a composite.**

## Goal
Attach `states[]` and compute `gate` for each item, from the point-in-time store. Pure functions,
deterministic on fixtures, no fitted weights anywhere.

## The 8 states (each → `FactorState{factor, level(Band), direction, evidence, valid}`)
1. **Cross-market lead** — US leader short-horizon move/revisions as a forward read on the A-share chain.
2. **Reversal** — short-horizon return vs own history, confirmed by turnover + Amihud illiquidity.
   `direction`: ran-up-hard → reversal-**DOWN** risk.
3. **Crowding** — turnover pct + Amihud + ETF flow + theme-vs-benchmark PE premium. A **risk** gauge.
4. **Demand / leading driver** — revision direction of end-demand capex/orders/contract prices.
5. **Valuation** — PE/PB/PEG vs the theme's **own** history (percentile).
6. **Trend gate** — price vs 200-day MA + 12-mo excess-return sign. A **downtrend filter** (hard cap).
7. **Low-vol / drawdown regime** — realized-vol percentile + current drawdown depth.
8. **Macro / liquidity dial** — 10-yr real yield, dollar, USD/CNY, Fed path → one book-wide regime.

## Rules
- Rank against the factor's **own** history into constant, economic-meaning bands (never tuned to P&L).
- A failed/stale factor → `valid=false`; the item still produces a valid (sparser) state bundle.
- `gate = capped` when below the 200-day MA, else `open`. This caps the lean in Phase 5; nothing may
  open a capped gate.

## TDD plan
- One pure-function test module per factor (`tests/unit/test_factor_*.py`): known fixture series →
  expected band + direction; broken/short series → `valid=false` (never raises).
- `tests/unit/test_trend_gate.py`: below-MA → `capped`; above-MA → `open`.
- Wire into the pipeline: `states[]` + `gate` attached to each item.

## System test
`states[]` present and non-empty for fixture items; at least one item `capped`; artifact valid +
deterministic.

## Done when
All factor unit tests + `make system` green; no composite introduced; `PROGRESS.md` + commit.
