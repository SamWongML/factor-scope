# PROGRESS — live handoff board

> Single source of truth for "where are we". A fresh session reads `CLAUDE.md` + this file + the
> current `phases/phase-N.md` and resumes. Update status + **NEXT ACTION** whenever a unit or phase
> closes, then commit. Statuses: `todo` · `wip` · `done`.

**Current phase:** Phase 7 — Scheduling, packaging & ops — **done**. All 8 phases complete.
**NEXT ACTION:** The roadmap is fully built (Phases 0–7). The next move is the **graduate tier**
(documented, not built — see `docs/ops/RUNBOOK.md` §graduate): ArcticDB bitemporal backtesting under
DSR/PBO/CPCV + parameter-stability, and an optional local vector store. Add only when backtesting
begins. Otherwise the engine is feature-complete against the spec; remaining work is live-source
wiring (behind `--live`) and real-provider hardening (`claude_code`), both opt-in and never in CI.

| Phase | Status | Closed by (commit) | System test |
|-------|--------|--------------------|-------------|
| 0 — Scaffold, contract, entrypoint, tracking | **done** | _initial Phase 0 commit_ | `make system` ✓ (7 tests) |
| 1 — Ingestion + point-in-time store | **done** | _Phase 1 commit_ | `make system` ✓ (5 tests) |
| 2 — Factor states + trend gate | **done** | _Phase 2 commit_ | `make system` ✓ (8 tests) |
| 3 — Connection graph + look-through | **done** | _Phase 3 commit_ | `make system` ✓ (10 tests) |
| 4 — Self-scoring loop | **done** | _Phase 4 commit_ | `make system` ✓ (11 tests) |
| 5 — Digestion: LLM provider + bull/bear | **done** | _Phase 5 commit_ | `make system` ✓ (16 tests) |
| 6 — Emerging radar funnel | **done** | _Phase 6 commit_ | `make system` ✓ (20 tests) |
| 7 — Scheduling, packaging & ops | **done** | _this commit_ | `make system` ✓ (23 tests) |

## Phase 7 — done
RED→GREEN→REFACTOR complete. Delivered:
- **Scheduling adapter** (`factor_scope/schedule/`, spec §11, D4): a thin **pure renderer** off a
  `ScheduleSpec`, no platform code on the critical path. `deploy.render_launchd_plist` builds a macOS
  launchd plist via `plistlib` (`StartCalendarInterval` daily at hour:minute, `RunAtLoad=false` — a
  batch, not a daemon; the Mac-mini production path) and `deploy.render_cron_line` the Linux
  alternative. Both deterministic and reviewable before install. Surfaced as `factor-scope schedule`
  (`--kind launchd|cron`, `-o` to write a file).
- **The one-shot nightly job** `factor-scope nightly` + `pipeline.nightly(config)` — ingest → compute
  → digest → write `dashboard.json` → append one ops record → persist calls. Differs from `run` only
  in **operable defaults**: a **durable** store/graph (so each night's leans accumulate as falsifiable
  calls for the §06 loop) and an append-only **JSONL run log**. Re-running the **same night is
  idempotent** (`_night_already_ingested` keys on tonight's positions `as_of` → skips re-ingest →
  artifact stays byte-for-byte, audit-trail scorer never double-counts); a new night ingests fresh.
- **Ops run log** (`schedule/runlog.py`): one `RunRecord` per run (`as_of`, start/end, per-list item
  counts, `n_abstain`, `provider`, `n_calls_logged` = tomorrow's scoring fuel, `output_path`,
  `cost_note`) appended as JSONL. It's *telemetry, not the artifact*, so wall-clock timestamps are
  allowed (injected `clock` for deterministic tests); only `dashboard.json` stays clock-free. The
  `cost_note` flags the **Agent-SDK credit** metering for `claude_code` from 2026-06-15 (sizing).
- **Docs:** `docs/ops/RUNBOOK.md` (the nightly job, run log, launchd/cron install, provider budget,
  the documented-not-built **graduate tier**); README gained a "Nightly setup" section + status.
  Decision **D11** records the design.
- Green: `make check` (142 passed, 2 live skipped), `make system` (23 tests). `factor-scope run
  --fixtures` still emits the same byte-for-byte artifact; two `nightly` runs of the same night
  reproduce `dashboard.json` exactly and append two ops records.

## Phase 6 — done
RED→GREEN→REFACTOR complete. Delivered:
- **Emerging funnel** (`factor_scope/emerging/`): the two-stage §07 funnel, deterministic + fixtures-
  first. **Stage A** (`stage_a.py`) qualifies an *industry* as four hard gates in spec order — signal
  strength (`acceleration + min(1, breadth/BREADTH_REF) − crowding`, with an acceleration floor),
  durability (broad-adoption ∧ path-to-profit ∧ fad-resistance), lead-chain corroboration, an
  investable wrapper — and reports the **first failing gate** so each stop is auditable. **Stage B**
  (`stage_b.py`, only on a cleared theme) scores each candidate fund on a **fixed scorecard**
  (methodology, overlap-with-core, cost, liquidity, tracking, concentration) → each criterion a
  `[0,1]` sub-score vs a constant reference, combined with **fixed economic-priority weights**
  (methodology + overlap the decisive pair, never tuned to P&L — D10) → a ranked **top 3**.
  `funnel.py` wires A→B per theme (name-ordered) into a `Shortlist`.
- **Overlap-with-core reuses §05** (`graph.lookthrough.look_through`) — candidate holdings ride the
  ordinary `fund_holdings` feed, so overlap is exact set arithmetic with **no new graph logic**; high
  overlap shrinks the score and can drop a fund from the top 3.
- **New adapters** `ingest/themes.py` (series `themes`) + `ingest/theme_funds.py` (series
  `theme_funds`), wired into `gather_fixture_readings` (loaded only if their CSVs exist). Fixtures:
  `themes.csv` (储能 clears; 元宇宙 fails durability — a fad; 可控核聚变 fails wrapper), `theme_funds.csv`
  (4 储能 candidates), and candidate holdings appended to `fund_holdings.csv`. The hand-placed 561160
  `emerging` **position** was removed from `positions.csv` — the `emerging` list is now the funnel's
  output.
- **Pipeline:** `_build_emerging` runs the funnel after the core look-through and emits one item per
  top-3 fund — §03 states/gate where price history exists (else gate `unknown` → the digest abstains),
  the Stage-A/Stage-B one-page comparison as `evidence`, and overlap as §05 `connections`. The
  existing `_attach_leans` then leans bull/bear over the shortlist and logs each as a call.
- Fixture story (spec's own lifecycle): **储能** clears Stage A; its **储能ETF** ranks **#1/4** on the
  scorecard (best methodology + liquidity, zero overlap) **but its trend gate is capped** (−18% vs the
  200-day MA) → the funnel says **do-not-chase → Avoid**; the optical-overlapping **光储龙头ETF**
  (holds 中际旭创, which my core already owns) is **dropped from the top 3** by §05; the two faint funds
  with no price history **abstain** (unknown gate). The core book's 8.4% look-through is unchanged.
- Green: `make check` (126 passed, 2 live skipped), `make system` (20 tests), `make run` shows the
  funnel; fixtures run reproduces `dashboard.json` byte-for-byte.

## Phase 5 — done
RED→GREEN→REFACTOR complete. Delivered:
- **Digest** (`factor_scope/digest`): an `LLMProvider` interface (`argue` both sides + `synthesize`)
  with structured `DigestInput`/`Case`/`Proposal`. Default **`FakeProvider`** is deterministic rules
  over the valid, non-neutral states — each casts a fixed-sign *risk* vote (reversal extreme-high =
  reversal-DOWN = bearish; downtrend bearish; tight macro bearish; calm bullish) — **not a fitted
  composite**. Bull marshals the positive votes, bear the negative (consider-the-opposite, isolated);
  synthesis nets them to a lean + a base-rate-anchored confidence. No network/keys/RNG → byte-for-byte.
- **Orchestrator** (`digest/orchestrator.py`) owns every hard guardrail *on top of* the provider, so
  even a misbehaving real model can't break them (D9): **abstain-when-blind** (unknown gate / <2 valid
  states / opposing extremes that cancel), the **trend-gate cap** (capped → no bullish lean: Hold for
  holdings, Avoid for watch), and the **scorecard** as a confidence-only channel (`confidence_nudge` +
  new `dampen_for_weak_pattern` — both can only lower a number, never touch action/state/gate). The
  descriptive fields (`text`, `evolution` from the prior call, `flip_trigger`, `invalidation`) are
  rendered deterministically from the *final* action so they always match the shipped lean.
- **Real providers, opt-in, never in CI:** `claude_code.py` (headless `claude -p ... --output-format
  json`; bull/bear system prompts shipped in `.claude/agents/bull.md`/`bear.md`; subprocess/json lazy)
  and `deepseek.py` — a **chore** client (`DeepSeekChores.summarise`, off the judgment path), *not* an
  `LLMProvider`; `get_provider("deepseek")` errors with a pointer to the real options.
- Pipeline `_attach_leans` digests each item (after states/connections/scorecard) and **logs every
  emitted lean via `scoring.log_call`** (a `Call` keyed `code:as_of`, horizon 30d, with the item's
  `state_pattern` tokens + `invalidation`) so next run's §06 loop scores *this* real call. Render now
  shows lean / evolution / flip-if / wrong-if per item.
- Fixture story (unchanged fixtures): **光通信** reads reversal `extreme_high` → **Trim**, and the
  mirror's overconfident `reversal:extreme_high` weak pattern dampens its confidence to **0.35**
  (the loop correctly distrusts the trim-the-winner persona); **储能** sits below its 200-day MA →
  gate `capped` → **Avoid**, never bullish; the rest **Hold**. Deterministic.
- Green: `make check` (110 passed, 2 live skipped), `make system` (16 tests), `make run` shows the
  leans; fixtures run reproduces `dashboard.json` byte-for-byte.

## Phase 4 — done
RED→GREEN→REFACTOR complete. Delivered:
- **Scoring** (`factor_scope/scoring`): a falsifiable `Call` (`{call_id, code, as_of, action,
  confidence, horizon_d, state_pattern[], invalidation}`) logged append-only into the point-in-time
  store (series `calls`, keyed by `call_id`, stamped with the night it was made → **immutable**).
  `scorer.py` is the mechanical next-day scorer: point-in-time **forward return** over the horizon
  (entry = price in force on the call date, exit = price in force at `as_of+horizon_d` days) vs the
  lean's stated direction → `Outcome ∈ {hit, miss, abstain}`. **No LLM, no memory, no opinion.** A
  settled call is immutable — prices arriving after the window can't move a resolved score.
- `scorecard.py` — pure functions over `(confidence, hit)` pairs: **Brier**, **Brier skill score**
  vs the base-rate forecaster (`b·(1-b)` reference; `None` when degenerate), reliability-by-confidence
  buckets (snapped to 0.1, over/under-confidence notes, thin buckets hidden), per-state-pattern
  `weak_patterns` (overconfident: stated ≫ realised). `build_scorecard` gates the whole block on a
  min sample (`DEFAULT_MIN_N=10`) — a thin record reports `n` only.
- **Guardrails (descriptive only):** the mirror's *one* channel into the digest is
  `confidence_nudge` — pulls a stated confidence toward its bucket's realised reliability, clamped
  `[0,1]`. It has no access to states or the gate; `FactorState` is frozen; `Scorecard` carries no
  `FactorState`/`GateState` field; the capped gate stays capped with the scorecard attached
  (`test_guardrails`, `test_scorecard_run`).
- Pipeline: `_attach_scorecard` scores all calls knowable tonight and shares one book-wide mirror
  onto every item (read-only; states/gate untouched). Render gained a `SELF-SCORING MIRROR` section.
- New ingest adapter `ingest/calls.py` (series `calls`, `state_pattern` is `|`-separated) wired into
  `gather_fixture_readings` (loaded only if `calls.csv` exists). Fixtures: `data/fixtures/calls.csv`
  (17 prior calls), regenerated by `scripts/gen_fixtures_phase4.py` (deterministic, reads the
  committed prices; outcomes fall out of real prices, not authored). Story: the confident
  `reversal:extreme_high` "trim the winner" pattern fights the optical-module uptrend → hits 0% →
  surfaces as an overconfident weak pattern; `trend:capped` avoid hits 100%.
- Leans land in P5 — P5 emits the lean **and** logs it via `scoring.log_call`, so this loop scores
  real calls from then on. Today it scores the prior-calls fixture.
- Green: `make check` (86 passed, 2 live skipped), `make system` (11 tests), `make run` shows the
  mirror; fixtures run reproduces `dashboard.json` byte-for-byte.

## Phase 3 — done
RED→GREEN→REFACTOR complete. Delivered:
- **Graph** (`factor_scope/graph`): a `GraphStore` `Protocol` + default append-only `DuckDBGraphStore`
  (`(:Fund)-[:HOLDS{weight,as_of}]->(:Security)`, file or `:memory:`), point-in-time at query time
  (same `QUALIFY` latest-as-of as the readings store). `build_graph_from_store` materialises HOLDS
  edges straight from the `fund_holdings` readings (no LLM). Engine choice = **D8** (Kùzu/Neo4j the
  documented swap). **No NetworkX / in-memory rebuilt-each-run graph.**
- **Look-through** (`graph/lookthrough.py`): exact set arithmetic — `look_through` returns the funds
  of mine holding a security (point-in-time) + my total weight `Σ(weight in fund × my portfolio
  weight in fund)`; `build_connections` surfaces a name only when ≥2 of my funds share it (the
  illusion-of-diversification catch), with a `↓` when any holder fund is at downside risk.
- Pipeline builds the graph (durable when `--graph-path` given, else in-memory from the store) and
  attaches `connections[]` + `connections_flag` per item; portfolio weights from `shares × NAV`. A
  falling name inherits the `↓` across the book. Render shows the overlaps. New `--graph-path` flag
  on `run`/`ingest`; `Config.graph_path`.
- Fixture story (unchanged fixtures): **中际旭创** is held by both the optical-module (561010) and
  comms (515880) ETFs → surfaces on both with look-through ≈ **8.4%**, flagged `↓` (561010 reads
  reversal-DOWN risk). 新易盛 / 中芯国际 are single-fund → not surfaced.
- Green: `make check` (57 passed, 2 live skipped), `make run` shows the look-through connections.

## Phase 2 — done
RED→GREEN→REFACTOR complete. Delivered:
- **Factors** (`factor_scope/factors`): `bands` (mid-rank `percentile_rank` + `rank_to_band` on
  constant 5/25/75/95 cut-points — economic meaning, never tuned to P&L), `window` (point-in-time
  series helpers: `price_navs`/`fred_values`, returns, rolling vol, drawdown, 200-day MA), and
  `battery` (the 8 states as pure `(FactorContext)->FactorState` functions in spec order + the hard
  `compute_gate`). **No composite anywhere.**
- Data-backed today (read from the store): **trend gate** (price vs 200-day MA → the gate),
  **reversal** (20d return vs own history; up-stretch → reversal-DOWN risk), **low-vol/drawdown**
  (rolling-vol percentile + drawdown), **macro dial** (DFII10 real-yield percentile, book-wide).
  Present-but-invalid until sources land: cross-market lead, crowding, demand, valuation
  (`valid=false`, never dropped, never raises).
- Pipeline attaches `states[]` + `gate` per item; render surfaces the active (non-neutral) reads.
- Fixtures gained real history (`scripts/gen_fixtures_phase2.py`, deterministic + seedless):
  `prices.csv` now ~220 weekdays/code (3 above their 200-MA = gate `open`, the energy-storage ETF in
  a ~33% drawdown below it = `capped`), `fred.csv` gained a 2-yr monthly DFII10 series.
- Green: `make check` (46 passed, 2 live skipped), `make run` shows the gate + states per item.

## Phase 1 — done
RED→GREEN→REFACTOR complete. Delivered:
- **Store** (`factor_scope/store`): `Reading` + `PointInTimeStore` protocol + append-only `DuckDBStore`
  (file or `:memory:`). `read_as_of` is point-in-time per key; `history` is the audit trail.
- **Ingest** (`factor_scope/ingest`): adapters for positions, prices, fund_holdings, fred, edgar —
  each a fixture backend (default) + an opt-in lazy `fetch_live` (behind `--live`, never in CI).
- Pipeline now `ingest`s into the store and `build_dashboard` reads it as-of the run date → items
  carry real `evidence[]` + a per-item `gain` (cost basis vs NAV). New `gain` field on the contract.
- New CLI `factor-scope ingest` (+ `--store-path` on `run`). Fixtures: `positions.csv`, `prices.csv`,
  `fund_holdings.csv`, `fred.csv`, `edgar.csv`, `manifest.json` (replacing `items.json`).
- CI + `make setup` now install the `store` extra. Green: `make test` (25 passed, 2 live skipped),
  `make lint`, `make typecheck`; `make run` shows store-sourced, point-in-time items.

## Phase 0 — done
RED→GREEN→REFACTOR complete. Delivered:
- Project tooling: `pyproject.toml` (uv, hatchling), `Makefile`, pytest markers, ruff, mypy strict, CI.
- **Contract** (`factor_scope/contract`): all `dashboard.json` models + JSON-schema export.
- Pipeline spine + `render` + `cli` (`run`, `schema`). Fixture `data/fixtures/items.json`.
- Process-tracking scaffold: `CLAUDE.md`, `docs/spec/`, `docs/plan/` (ROADMAP, PROGRESS, DECISIONS,
  phases/), `docs/ARCHITECTURE.md`.
- Green: `make test` (7 passed), `make lint`, `make typecheck`; `make run` prints the artifact.

## Notes for the next session
- Keep `factor-scope run --fixtures` green at every commit (the invariant).
- The point-in-time store is the seam: factors read `prices`/`fred` via `store.history(...)` filtered
  to `as_of <=` the run date (see `factor_scope/factors/window.py`). Adding a new data-backed factor
  = enrich the fixtures + flip its `_unavailable(...)` stub to a real band; no new ingestion needed
  unless a brand-new source is required.
- The connection graph is durable on disk (`DuckDBGraphStore`, `--graph-path`) or built in-memory
  from the readings store at run time when no path is given (mirrors the readings-store pattern).
  Look-through is exact set arithmetic, point-in-time at query time; Phase 6's emerging
  overlap-with-core reuses `graph.lookthrough.look_through` directly (candidate holdings ride the
  ordinary `fund_holdings` feed) — no new graph logic. EDGAR (US lead chain) is deliberately *not* in
  the book graph (no weights, different universe) — see D8.
- The self-scoring loop (§06) is wired and LLM-free: `scoring.score_calls(store, as_of)` scores every
  call knowable tonight by point-in-time forward return; `build_scorecard` rolls them into the mirror
  the pipeline attaches to each item. **Phase 5 now closes the loop**: `_attach_leans` logs every
  emitted lean via `scoring.log_call` (`Call` keyed `code:as_of`, `state_pattern` tokens, `horizon_d`,
  `invalidation`) so next run scores *real* calls. The mirror reaches a lean only through the two
  confidence-only functions (`confidence_nudge` + `dampen_for_weak_pattern`) — never the action/gate.
- The digest seam: `digest.digest_item(provider, DigestInput)` runs bull/bear→synthesis with the
  gate/abstain/scorecard guardrails and returns a `DigestResult`. Phase 6's emerging items flow
  through the same `_attach_leans` path, so the trend-gate cap (D9) enforces do-not-chase on a capped
  shortlisted fund and faint funds (gate `unknown`) abstain — no separate emerging digest needed.
- The emerging funnel (`factor_scope/emerging/`, spec §07) is the seam for any future weak-signal
  work: Stage A gates a theme, Stage B's fixed scorecard ranks its funds (D10). New themes/funds are
  pure fixture edits (`themes.csv`, `theme_funds.csv`, candidate rows in `fund_holdings.csv`); live
  theme discovery (BERTopic / LLM tagging) is the documented `--live` swap, not wired in CI.
- Live fetchers stay behind `--live`, lazily imported, never in CI (smokes skip unless
  `FACTOR_SCOPE_LIVE=1`). The `store` extra (duckdb) is installed by CI + `make setup`.
- Fixtures are regenerated, not hand-edited: `uv run python scripts/gen_fixtures_phase2.py`
  (deterministic + seedless, so the artifact stays byte-for-byte reproducible).
