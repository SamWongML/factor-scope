# factor-scope — Roadmap, Architecture Map & Research Context

This is the durable companion to `CLAUDE.md`: where `CLAUDE.md` states the rules, this states the
**direction** — the North Star we are building toward, the invariants that stay vs. change, the
cross-cutting architecture, the repository map, and the dependency-ordered upgrade issue set
(`U01`–`U17`). It supersedes the earlier point-in-time gap-analysis report.

> The upgrade work is tracked as GitHub issues `U01`–`U17` (one issue = one Claude Code session =
> one branch → one PR → `make check` green). The tables below are the index; each issue body carries
> the full Goal / Why / Depends / Touch / Acceptance / Out-of-scope.

---

## North Star (what we are building)

A **local-first, online-by-default, market-agnostic** decision-support engine. Four stages, human at
both ends; the engine never trades.

1. **Theme discovery (separate from the nightly run).** A user/cron-triggered job researches emerging
   industries, attaches **evidence/materials** per theme, and pushes a candidate list to a lightweight
   frontend. The user **reads the materials and keeps/drops** themes. Only the **curated** list feeds
   the nightly run.
2. **Fund universe + mapping.** Retrieve the **full** China fund universe (generically, any configured
   market) and **infer** each theme's candidate funds from holdings **overlap + return correlation** —
   no hand-curated theme→fund table.
3. **Per-product bull/bear index.** A **multi-stage funnel** trims each theme's candidates to a
   **top 3** cheaply; then the existing **"seat" debate** (bull/bear/synthesis Claude Code subagents)
   runs on a **deep-thinking model** over the finalists, calibrated and de-biased.
4. **Human decides.** The engine never places orders; the user makes the final call per item.

---

## Invariants that STAY

- **Append-only, point-in-time store.** Every fact is a dated `Reading`; `read_as_of(series, D)`
  returns only what was knowable on `D`. Later disclosures never rewrite earlier reads.
- **Determinism at the reasoning layer.** Given a **frozen snapshot** of Readings + a fixed mock
  provider, `dashboard.json` reproduces **byte-for-byte**.
- **Hard caps live in the orchestrator**, on top of model output — an overconfident model cannot open
  a capped gate. Enforcement stays server-side of the LLM.
- **Constants chosen for economic meaning, never tuned to P&L.** Invalid inputs **degrade**
  (`valid=False`), never raise.
- **The engine never places orders.**

## Invariants that CHANGE (explicit decisions)

- **Online-by-default, not offline-by-default.** Live data + real providers are the normal path;
  **offline is a test mode only** (fixtures + mock provider), selected by `--offline` /
  `FACTOR_SCOPE_OFFLINE=1`. Determinism is preserved by the *snapshot boundary* + mocks, not by
  avoiding the network. *(Landed in `U05`.)*
- **Market-agnostic, not A-share/ETF-hardcoded.** Markets, universes, theme sources, factor sets, and
  providers sit behind config-driven interfaces. A-share is the *first* adapter (no speculative
  multi-market code — YAGNI).
- **Per-product index, not "promote at most one."** A bull/bear index is produced for **each**
  finalist; promotion becomes presentation.

---

## Cross-cutting architecture (Phase 0; referenced by later issues)

- **Snapshot boundary (`U03`).** Split into (1) a **non-deterministic research/ingest job** that
  fetches web/text/market data + LLM-derived fields and **writes them as dated Readings**, and (2) a
  **deterministic reason-over-snapshot pipeline** (factors → graph → funnel → seats → artifact)
  reading only the frozen snapshot. This reconciles online-default with byte-for-byte determinism and
  blocks look-ahead.
- **Model tiering (`U10`).** `deepseek-v4-flash` = bulk extraction/summarization/coarse scoring;
  `deepseek-v4-pro` = mid-tier structured judgments/ranking; the **deep-thinking model** (Claude
  Opus-class, or `deepseek-v4-pro` Max-reasoning) runs **only the final seat debate**. Use **explicit
  model IDs** (`deepseek-chat`/`deepseek-reasoner` aliases deprecate **2026-07-24**).
- **Generalization seams (`U02`).** `Market` / `UniverseSource` / `ThemeSource` / `PriceSource`
  protocols + reuse the existing `Provider` protocol; A-share as the first concrete adapter.
- **Anti-hype guardrails (`U13`, Ben-David).** Specialized/thematic ETFs lose ~30% risk-adjusted over
  5 years because they launch at the **hype peak** on overvalued underlyings. Crowding / valuation /
  run-up / launch signals must be able to **veto**, and the universe must be **survivorship-aware**.

---

## Repository map

- `factor_scope/contract/` — pydantic models for `dashboard.json` + JSON-schema export. **The spine.**
- `factor_scope/pipeline.py` — `ingest()` fills the store; `build_dashboard`/`run` read it → `Dashboard`.
- `factor_scope/ingest/` — pull CN prices/holdings + US FRED/EDGAR + `positions.csv` into the store.
- `factor_scope/store/` — append-only, point-in-time DuckDB readings log.
- `factor_scope/factors/` — 8 descriptive factor states + the 200-day trend gate (`battery.py`,
  `bands.py`, `window.py`).
- `factor_scope/graph/` — holdings graph + deterministic fund look-through (`store.py`,
  `lookthrough.py`).
- `factor_scope/digest/` — bull/bear debate → calibrated lean (`provider.py`, `fake.py`,
  `claude_code.py`, `deepseek.py`, `orchestrator.py`).
- `factor_scope/scoring/` — rolling Brier scorecard (the self-scoring mirror).
- `factor_scope/emerging/` — Stage-A screen → Stage-B ranking → cheap-LLM re-rank to top 3
  (`stage_a.py`, `stage_b.py`, `shortlist.py`, `funnel.py`).
- `factor_scope/schedule/` — launchd plist + cron line + run log.
- `factor_scope/cli.py` typer app · `factor_scope/render.py` terminal view · `factor_scope/config.py`.
- `factor_scope/history.py` — immutable per-night artifact record (`out/dashboards/<as_of>.json`) ·
  `factor_scope/serve.py` — the read-only history API a frontend consumes (the U16 viewer's seam),
  deriving the night index live from those files.
- `data/fixtures/` committed sample data · `tests/{unit,integration,system}` (markers in
  `pyproject.toml`).
- `.claude/agents/` — the `bull` / `bear` / `synthesis` subagent "seats."

---

## The upgrade issue set (`U01`–`U17`)

Each issue is sized to one Claude Code session. Labels use two uniform axes — a bare **priority**
(`p0` / `p1` / `p2` / `p3`) plus a single-word **area** (`foundation` / `universe` / `factors` /
`discovery` / `reasoning` / `frontend` / `cross-cutting` / `deferred`).

| ID | Title | Labels | Depends on |
|----|-------|--------|------------|
| **U01** | CI invokes `make check` | `p0` `foundation` | — |
| **U02** | Generalization seams (provider/source protocols + config) | `p0` `foundation` `cross-cutting` | — |
| **U03** | Snapshot boundary (research/ingest writes Readings; deterministic reasoning) | `p0` `foundation` `cross-cutting` | U02 |
| **U04** | Graph edge model: idempotent + temporal (`valid_from`/`valid_to`) | `p0` `foundation` `cross-cutting` | U03 |
| **U05** | Online-by-default flip + pin live extras / `uv.lock` | `p0` `foundation` `cross-cutting` | U03 |
| **U06** | Full fund universe + AkShare/AkTools live ingest | `p1` `universe` | U02, U03, U04 |
| **U07** | Complete the factor battery (4 stubbed states + reversal) | `p1` `factors` | U06 |
| **U08** | Inferred theme→fund mapping (overlap + return correlation) | `p1` `universe` | U04, U06, U07 |
| **U09** | Theme-discovery service (BERTopic online + LLM-populated, evidence-rich) | `p1` `discovery` | U02, U03, U05 |
| **U10** | Provider tiering: DeepSeek V4 tiers + deep-think seats + stream-json cost envelope | `p1` `reasoning` | U02, U05 |
| **U11** | Multi-stage fund shortlisting funnel → top 3 | `p1` `reasoning` | U08, U07, U10 |
| **U12** | Per-product bull/bear seats: parallel, evidence+as_of brief, de-bias + calibration | `p1` `reasoning` | U11, U10, U07 |
| **U13** | Anti-hype guardrails (Ben-David) + survivorship-aware universe | `p1` `cross-cutting` | U06, U07, U11/U12 |
| **U14** | Freeze the Scorecard contract model | `p2` `reasoning` | U11, U12 |
| **U15** | Cost telemetry + multi-provider budget guard | `p2` `cross-cutting` | U10, U03 |
| **U16** | Frontend: theme curation (keep/drop) + dashboard viewer | `p2` `frontend` `discovery` | U09, U03, U12 |
| **U17** | Graduate-tier robustness (DSR / PBO / CPCV / ArcticDB) — deferred | `p3` `deferred` | U13 |

### Execution order (phases; within a phase, top-to-bottom)

```
Phase 0  U01 ─ U02 ─ U03 ─ U04 ─ U05            [foundations + quick win]
Phase 1  U06 ─ U07                              [data + factors]
Phase 2  U08 (needs U04,U06,U07) ; U09 (needs U02,U03,U05)
Phase 3  U10 ─ U11 (needs U08,U07,U10) ─ U12 (needs U11,U10,U07)
Phase 4  U13 ; U14 (needs U11,U12) ; U15 (needs U10) ; U16 (needs U09,U12)
Deferred U17 (needs U13)
```

Critical path: **U02 → U03 → U04 → U06 → U07 → U08 → U10 → U11 → U12.** U01/U05 and parts of Phase 4
run alongside.

### Old → new map (the original 17 issues, #27–#44)

| Old | Title (abridged) | → New |
|-----|------------------|-------|
| #27 | edges not idempotent (PK + ON CONFLICT) | U04 |
| #28 | pin live extras + uv.lock | U05 |
| #29 | `--output-format stream-json` (cost envelope) | U10 (consumed by U15) |
| #30 | run bull/bear seats in parallel via Task tool | U12 |
| #31 | feed evidence + as_of into the seat brief | U12 |
| #32 | 4 stubbed factor states | U07 |
| #33 | reversal turnover/Amihud | U07 |
| #34 | reconcile AkShare endpoints | U06 |
| #35 | BERTopic signal-strength | U09 |
| #36 | AkTools / messy-web-crowding / lead-chain | U06 (market) + U09 (theme) |
| #38 | cost telemetry + $20/mo guard | U15 (re-scoped multi-provider) |
| #39 | freeze Scorecard contract | U14 |
| #40 | temporal edge fields | U04 |
| #41 | wire or remove DeepSeek | U10 (wire in, tiered) |
| #42 | CI call `make check` | U01 |
| #43 | `Theme.base_level` dead field | U09 (wire in) |
| #44 | DSR/PBO/CPCV/ArcticDB | U17 + near-term subset U13 |

Nothing is dropped: every original issue survives as a re-scoped U-issue, a merge, or a deferral.
`#41`/`#43`'s "wire or remove" both resolve as **wire in**.

### Session conventions (every issue)

- Branch `feat/<phase>-<slug>`; one PR; **`make check` (ruff + mypy strict + full suite) green**.
- Work **test-first** (RED → GREEN → REFACTOR); the entrypoint stays runnable at every boundary.
- New live/network deps imported **lazily**; the offline test path never shells out.
- Tests marked `unit` / `integration` / `system` per `pyproject.toml`. Touch only the named files; if
  scope grows, split the issue.

---

## Research context pack (findings → implementation implications)

### §1 — Theme discovery / weak signals (→ U09)

- **Online topic modeling classifies trajectories, not just topics.** *BERTrend* (arXiv 2411.05930)
  runs **BERTopic in an online-learning setting** and labels each topic **noise / weak signal /
  strong signal** by its popularity trend over time → use it to derive `acceleration` / `base_level`
  / `breadth`; do not hand-author them. Ingest news, social, research/tech journals, filings.
- **Hardest problems are noise-vs-signal, context ambiguity, long latency** → keep a human-validation
  step (the keep/drop UI, U16); don't auto-promote a topic to a tradeable theme.
- **Expert-in-the-loop + modularity is the durable pattern.** *Detecting Emerging Technologies*
  (arXiv 2205.05449), *WISDOM* (arXiv 2409.15340): transformer keyword extraction + weak-signal
  scoring validated by domain experts, re-applicable across fields → matches U02 generalization +
  U09 evidence payloads.

### §2 — What qualifies as a "theme" (→ U09 Stage-A gates)

- **BlackRock's 3 criteria:** a theme is (i) **dynamic** (top themes shift; needs a timely, scalable
  identification method), (ii) **impacts generally unrelated companies** (capturing it requires
  anticipating new cross-stock linkages that sector/industry groupings miss), (iii) **persists / has
  driven returns** → Stage A already gates durability; add a **cross-sector-breadth test** (breadth
  across distinct industries, not just sources).

### §3 — Full CN fund universe (→ U06; AkShare, lazy-imported)

- `fund_name_em` → **all funds** (code, type, short name). `fund_etf_spot_em` → on-exchange ETF
  universe + ETF share/AUM (总市值 / 最新份额).
  `fund_portfolio_hold_em(symbol, date)` → **fund holdings** (the graph edges).
  `fund_individual_analysis_xq` → Xueqiu per-fund analysis → "all funds in China" is data
  engineering, not research. Capture **inception + delisting** dates for U13 *(landed: the
  exchange-traded ranking carries 成立日期; a delisting is disclosed when a fund leaves the feed)*.
  Mark missing scorecard inputs `valid=False`.

### §4 — Theme→fund mapping, the professional way (→ U08)

- **Guojin (国金) sector-rotation desk** builds its pool from market attention + product
  investability, using Shenwan (申万) level-1.5 classification, and establishes
  one-to-one / one-to-many / many-to-one mappings via holdings overlap (重合度) and return correlation
  (涨跌幅相关性) → exactly U08: infer the mapping from overlap (`funds_holding`) + rolling return
  correlation; no pre-tagged table.
- **Diffusion index (扩散指数):** share of an industry's constituents in uptrend (MA/ROC), top-6,
  equal-weight mapped ETFs, monthly rebalance; a low-volatility variant divides by return volatility
  to cut drawdown → a computable instantiation of Stage-A "breadth"; the vol-adjustment is a cheap
  drawdown win.
- CN desks also use **crowding (拥挤度), capital flow, sentiment, chip-distribution** → confirms
  crowding as a first-class risk gauge (U07/U13).

### §5 — The thematic-ETF trap (→ U13; the single most important guardrail)

- **Ben-David, Franzoni, Kim & Moussawi, "Competition for Attention in the ETF Space,"** *Review of
  Financial Studies* 2023 (NBER w28369): specialized/thematic ETFs **lose ~30% risk-adjusted over
  their first 5 years (~−6%/yr)**, driven **not by fees** but by **overvaluation of the underlyings
  at launch** — providers launch at the **peak of theme hype**. Morningstar: **~10% of thematic funds
  beat the broad index at 10 years** → crowding/valuation/run-up/launch-date must be able to
  **veto**; the universe must be **survivorship-aware**; treat "high attention + high valuation" as a
  *late* signal, not an early one.
- **Decision — ETF flow is a U13 concern, not a `crowding` sub-input.** `crowding` stays a single
  turnover-percentile state (换手率 ranked vs its own history). It does **not** absorb ETF
  flow / 份额 growth, for three reasons: (i) the no-composite rule forbids blending flow + turnover
  into one band anyway; (ii) best practice keeps them separate — MSCI's factor-crowding model carries
  neither turnover nor flow, and CN desks split rotation into `景气度 + 资金流 + 拥挤度` (three axes,
  crowding = the turnover-based risk overlay); (iii) raw net flow is largely collinear with turnover
  (its orthogonal slice is the *signed/unexpected* residual, which a single ranked signal can't
  isolate) and is operationally fragile point-in-time (Δshares×NAV is lagged + restated; SSE stamps
  份额 post-close vs SZSE pre-open; stale NAV on cross-border; AP-arbitrage/"Ponzi" flow noise). The
  over-extension content flow *does* carry — thematic launch-at-peak overvaluation — is already
  reached by `valuation` (PE) + the launch/survivorship work here. If flow is pursued, add it as its
  **own** signed shares-outstanding-growth state under U13 (with first-printed 份额 snapshotting and
  SSE/SZSE as-of reconciliation), never folded into `crowding`.

### §6 — Multi-agent bull/bear debate (→ U12; the "seat" model)

- **TradingAgents** (arXiv 2412.20138): 5-layer firm — analysts → **Bull/Bear debate** → trader →
  risk committee → PM. Key claim: **two LLMs forced to advocate each side + a third synthesizer beats
  one LLM weighing both sides**, because single-model setups cherry-pick evidence to confirm an
  initial thesis (confirmation bias). Uses **deep_think vs quick_think** tiers + Pydantic-typed
  outputs. Caveats: short backtest, expensive, small models produce noisy/repetitive debate → adopt
  tiering (U10) + structured per-product outputs; treat the index as decision *support*.
- **Debate beats single-advocate, and judges are overconfident.** *Debating with More Persuasive
  LLMs* (arXiv 2402.06782): debate outperforms consultancy at all coverage rates, and LLM judges are
  poorly calibrated → keep the adversarial seats; lean on the Brier scorecard + the orchestrator gate
  to discipline overconfidence.

### §7 — LLM-as-judge biases & mitigations (→ U12 synthesis seat)

- Documented biases: **verbosity, position, overconfidence, sycophancy/self-preference.** Mitigations
  that map onto a bull/bear index: **order-swap the two cases and average**; **rubric-conditioned
  scoring** (which case better satisfies explicit criteria X/Y/Z, not "which is better");
  **reference-guided grading** (ground in dated evidence — the seat brief carries `evidence` + `as_of`);
  **calibrate against a small gold set (30–50 human-labeled examples)** → bake swap+average + rubric
  into `synthesis.md`; the seat brief is reference-grounded + point-in-time.

### §8 — Shortlisting funnel & LLM cascades (→ U11)

- **Canonical 3-stage funnel: candidate generation → ranking → re-ranking** (arXiv 2303.04689,
  2508.02242); each stage narrows the set while using progressively more expensive models; re-ranking
  encodes business rules (diversity, freshness, de-dup) → universe∩mapping + coarse filters →
  deterministic scorecard → cheap-LLM + rule re-rank → top 3.
- **LLM cascades: cheap-first, escalate on low confidence.** FrugalGPT reports up to 98% cost
  reduction; Select-then-Route narrows the model pool then cascades → use DeepSeek V4 for the
  re-rank; escalate to the deep-thinking seats only for the 3 finalists.

### §9 — DeepSeek V4 facts (→ U10; verified 2026-06-08)

- **Released 2026-04-24.** Two variants, both **1M-token context, 384K max output, OpenAI- &
  Anthropic-compatible endpoints, open weights:**
  - **`deepseek-v4-flash`** — ~**$0.14 / $0.28** per 1M tokens (in/out); thinking + non-thinking
    modes → bulk extraction, summarization, coarse scoring, agent subtasks.
  - **`deepseek-v4-pro`** — ~**$0.44 / $0.87** per 1M (promo; original $1.74/$3.48); function calling
    + structured output; Max-reasoning mode → mid-tier ranking / structured judgments / candidate
    deep_think.
- **Legacy aliases `deepseek-chat` / `deepseek-reasoner` deprecate 2026-07-24** (they map to
  V4-Flash modes) → pin **explicit model IDs** in config; do not rely on aliases.

### §10 — CN community pragmatics (→ U11/U13 guardrails; keep constants economic)

- ETF rotation reduces to **Pool (what to buy) + Momentum/timing (when)**, and practitioners are
  blunt that it carries **large overfitting risk + small capacity, which is why quant funds don't run
  it** → preserve "constants chosen for economic meaning, never tuned to P&L"; resist fitting the
  scorecard to returns; this is also why U17 (DSR/PBO/CPCV) exists.

---

Ops (nightly job, scheduling, provider budget): `docs/ops/RUNBOOK.md`.
