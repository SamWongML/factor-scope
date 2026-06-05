# SPEC — Wealth-Assistant Engine v4 (distilled)

> Source of truth: `wealth-assistant-engine-v4.html` (in this folder). This is a faithful, terse
> distillation for fast session onboarding. When in doubt, the HTML wins.

## One-line
A lean, local-first, single-user **nightly batch** that turns free market data into one dated
`dashboard.json` — three lists, factor *states*, exact holdings connections, a self-scoring
scorecard, and a bull/bear-then-synthesis lean — which a human reviews each morning. It shortens
your **lag**, not your **risk**. The engine never places orders.

## The six layers (L1→L6)
- **L1 Ingestion** — curated, free, dual-sourced. CN: AkShare + Baostock + Mootdx (prices,
  fundamentals, quarterly fund holdings; no Tushare credit gate). US lead: EdgarTools (13F + monthly
  N-PORT holdings) + FRED (rates/dollar/liquidity). Your positions: a local `positions.csv`
  (no personal-account API exists for 理财通 etc.).
- **L2 Store (point-in-time)** — DuckDB + Parquet (append-only; → ArcticDB bitemporal when
  backtesting). The connection graph lives in a durable on-disk graph DB (Neo4j Community /
  LadybugDB / FalkorDB), quarterly snapshots. Never NetworkX / rebuilt-in-RAM.
- **L3 Compute (the legible core, all local)** — descriptive **factor states** (no composite); the
  **exact** holdings look-through; the weak-signal **emerging radar**; the **self-scoring** step.
  Cheap chores (reformat/summarise) offload to DeepSeek V4.
- **L4 Digestion** — **Claude Code, headless** (`claude -p`). A small **bull/bear** subagent team
  (isolated contexts) argues both sides; a synthesis seat anchors a base rate, reads the scorecard +
  connections, enforces the gate, and emits the lean. Abstain when blind.
- **L5 Dashboard contract** — one `dashboard.json` per run: per-item state bundle, calibrated lean +
  evolution + flip-trigger, `connections[]`, evidence slots, and a `scorecard` block.
- **L6 Morning review** — the human reads the three lists + connection map + scorecard, and acts
  only when a lean + flip-trigger + sizing line up. No feedback is given to the model.

## §03 Factor states (the numeric core, deliberately un-optimized)
Each raw input → a **descriptive state**: ranked against its *own* history into a logic-based band
(`extreme_low … extreme_high`), with a `direction` and a `valid` flag. **No weighted composite.**
A failed/stale factor → `valid:false` and is ignored (missing ≠ bad). Rules: rank-against-own-history
in constant bands; read `direction` (in A-shares a stretched up-move = reversal-DOWN risk); a handful
is enough. The battery (8 states): 1 cross-market lead (US→A-share), 2 reversal (turnover + Amihud),
3 crowding (risk gauge, not alpha), 4 demand/leading driver, 5 valuation (vs own history), 6 trend
gate (200-day MA — a hard cap), 7 low-vol/drawdown regime, 8 macro/liquidity dial (book-wide).

## §05 Connection graph (the motivating feature)
Deterministic, exact, local, auditable look-through:
`(:Fund)-[:HOLDS{weight,as_of}]->(:Security)-[:EXPOSED_TO]->(:Driver|:Theme)`, built straight from
holdings feeds (no LLM). Answers "B is falling — who else of mine holds it, and my total look-through
weight?" Catches the illusion of diversification. Temporal/point-in-time, quarterly snapshots.
GraphRAG is **dropped** (slow/costly/complex); for fuzzy second-order links, hand the digest the
falling node's one-hop neighbourhood as plain text.

## §06 Self-scoring (replaces v3's feedback/memory loop)
Each lean is a falsifiable claim (lean + confidence + horizon + invalidation). Next day, score it
mechanically: forward return vs stated direction → hit/miss/abstain (no LLM, no memory, no opinion).
A rolling **scorecard**: Brier score, Brier **skill** vs base rate, reliability-by-confidence,
per-state-pattern hit-rate. Shown to tomorrow's digest as a **mirror** (may widen/narrow confidence)
— **descriptive only**, gated on a minimum sample + rolling window.

## §07 Emerging funnel (industry → top-3 funds)
**Stage A** qualify the *industry*: signal strength (acceleration + breadth − crowding), durability
(broad adoption + path to profit + fad-resistance), lead-chain corroboration, investable-wrapper
exists. **Stage B** (only if A passes) screen the theme's funds on a fixed scorecard
(methodology/pure-play, overlap-with-core via §05, crowding, cost, liquidity/size, tracking,
concentration) → ranked **top 3**. The digest argues bull/bear over the shortlist; promote ≤1.

## §08 Decision layer
LLMs are overconfident (worse in finance/non-English). Three stacked defences: states-not-composite,
the two-sided bull/bear debate (isolated contexts, consider-the-opposite), and the §06 scorecard.
Discipline: base-rate first; allocation > timing (sin only a little); evidence-grounded with
auto-downgrade (stale/single-source/conflict/forum-only); abstain when blind. Cheap chores →
DeepSeek V4 (Flash bulk, Pro for heavier summaries); judgement stays on Claude Code.

## §09 Storage rule
**Point-in-time, everywhere.** Prices, factor states, scored calls, and the holdings graph all carry
an as-of stamp and are append-only. Reasoning tonight sees only what was knowable tonight.

## §10 Durability
Little to overfit by design (states, not fitted weights). When backtesting: Deflated Sharpe Ratio,
Probability of Backtest Overfitting, purged/embargoed CV (CPCV), parameter-stability, after-cost +
point-in-time always. The self-scoring loop is the durability mechanism for the judgment layer.

## §11 Build order (the spec's own phases; this repo decomposes them — see ROADMAP)
1. States + nightly job → `dashboard.json`. 2. Connections (look-through). 3. Self-scoring.
4. Bull/bear + emerging funnel (+ DeepSeek chores). 5. Graduate: ArcticDB bitemporal backtesting.

## §13 Limits
Cannot predict tops/bottoms, won't trust a recalled number, won't learn finance from you, won't
replace your judgment. The gift is better-timed, fewer, calmer trades — each with a written reason,
each checked against how the last ones resolved. Most mornings the right action is none.
