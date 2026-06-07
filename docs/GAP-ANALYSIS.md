# factor-scope — Gap Analysis & Production-Readiness Report

**Date:** 2026-06-07 · **Baseline:** `main` @ `3d42c72` · **Suite:** `make check` green (198 passed, 7 skipped; ruff clean; mypy strict clean over 43 files).

**Authority:** `docs/spec/wealth-assistant-engine-v4.html` (the v4 spec).
**Method:** spec read in full; codebase mapped by parallel exploration agents; every load-bearing claim re-verified by direct file read; external dependencies live-checked on the web (June 2026).

## Scope of this review (as agreed)

- **Production lens — "harden within the spec frame."** Production-grade here means a *robust single-user, local-first nightly job on one Mac mini*: reliability, idempotency, error-recovery, run observability, secrets/key handling, dependency pinning, data integrity. **No** SaaS / cloud / multi-tenant infrastructure is in scope.
- **Gap basis — "strict literal conformance."** If the spec names a specific technology or approach and the repo uses a different one, that is a gap (**DIVERGENT**) *even when the substitute meets the intent*. A remediation to adopt the named technology is recorded regardless.
- **Research — dependency currency + a deep dive on `/loop`** as a possible Agent-SDK replacement so the service draws only on Claude Code Pro-plan quota.

Classifications: **DIVERGENT** (spec names X, repo does Y) · **MISSING** (spec feature absent) · **PARTIAL** (present but incomplete) · **AT-RISK** (production-hardening gap, not a spec gap).

---

## 1. Executive summary

The repo is a **faithful, well-tested skeleton with its hard invariants genuinely enforced** — determinism, append-only store, the 200-day trend-gate hard cap, states-not-a-composite, abstain-when-blind, and the scorecard guardrails are all real and test-covered (see §6). The gaps are concentrated in three places:

| Severity | Count | Theme |
|---|---|---|
| **P0** | 1 | The real LLM path can crash the entire nightly run with no trace. |
| **P1** | 12 | Spec-named technologies substituted (graph DB, `stream-json`, parallel seats); 4/8 factors stubbed; ingest/emerging surfaces not built; idempotency + dependency pinning gaps. |
| **P2** | 7 | Budget telemetry, model freeze, temporal edges, dead wiring, CI/doc hygiene. |

The single most important operational finding: **`claude -p` is the only viable unattended path, and after 2026-06-15 it bills against a separate $20/mo Pro "Agent-SDK credit" pool — not your interactive Pro quota.** `/loop` cannot replace it. This makes nightly **cost telemetry against the $20 ceiling** a real production requirement (P2-1).

---

## 2. The `/loop` deep dive — can it replace the Agent-SDK so the service runs on Pro-plan quota?

**Short answer: no.** `/loop` cannot run the unattended nightly job, and the quota it would draw on is not the one that matters after June 15.

### 2.1 What `/loop` actually is
`/loop` (the bundled Claude Code skill) runs **in-session only**. It is *process-bound, not system-bound*: if the terminal closes or the session ends, every active loop is wiped, and a new session starts with no scheduled jobs. Recurring tasks **auto-expire 3 days after creation**, fire one last time, then delete themselves (min interval 1 minute, max 50 tasks/session). If a loop step touches a tool that isn't pre-permitted — shell, file write, network — it **stops to ask for confirmation** (which breaks unattended operation) or fails silently. Anthropic's own framing: it is for *short-term, in-session automation while you are actively working*, **not** long-term unattended jobs that persist across reboots.

A factor-scope night needs exactly the opposite: it must survive a closed laptop and reboots, run for months unattended, and freely use shell + file-write + network (ingest, DuckDB, writing `dashboard.json`). `/loop` fails on every one of those.

### 2.2 The quota that actually matters (the June 15 change)
Starting **2026-06-15**, Claude Agent SDK **and `claude -p` headless usage** no longer count toward your plan's interactive usage limits on Pro/Max/Team/Enterprise. Instead they draw from a **separate monthly Agent-SDK credit pool**: **$20 for Pro**, $100 for Max 5×, $200 for Max 20×. It refreshes each billing cycle, **expires monthly (no rollover)**, and can't be pooled or transferred. Interactive limits stay reserved for interactive Claude Code / Cowork / Claude.

Implication for the stated goal ("let the whole service consume only quota in my Pro plan"):

- The nightly digest uses `claude -p` headless → it bills against the **$20/mo Pro Agent-SDK pool**, not interactive Pro quota.
- `/loop` *would* draw on interactive quota — but it cannot run unattended, so it is not an option for the nightly job.
- **There is no path to running an unattended headless nightly purely on the interactive Pro subscription after June 15.** The $20/mo Agent-SDK credit is the real ceiling.

### 2.3 Recommendation
1. **Keep launchd + `claude -p` headless** (the current architecture). It is the only unattended-capable path; `/loop` is not a substitute. *(No change required — this confirms the existing design.)*
2. **Budget the digest against $20/mo.** Estimate monthly spend = nights × items/night × 3 seats (bull/bear/synthesis) × tokens/seat. If it exceeds $20, the levers are: a cheaper `--model`, fewer seats, real-digest only on a subset of nights (fake provider otherwise), or fewer items.
3. **Add cost telemetry to the run log** (see P2-1) so you can watch the monthly burn-down and never get silently cut off mid-month.
4. **Before June 15, confirm the exact Pro figure and eligibility** at the Help Center article (sandboxed from this review): `support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan`.

---

## 3. Dependency currency (live-checked, June 2026)

The headline: **every live dependency is actively maintained**, so the live-extras gap is that they are **unpinned**, not that they are abandoned — *except* the graph layer, where the code's own "production swap" target was archived.

| Dependency | Status (June 2026) | Note |
|---|---|---|
| **Kùzu** (graph) | **Archived Oct 2025** | Original repo no longer maintained. The `graph/store.py` docstring names "Kùzu / Neo4j" as the production swap — that target is dead. |
| **LadybugDB** | **Active** — v0.16.1 (2026-05-04), v0.17.x on PyPI | The **maintained continuation of Kùzu**, embedded, openCypher, DuckDB/Arrow/Parquet-interoperable — **and it is spec-named.** This is the precise target for the graph remediation (P1-1). |
| akshare | Active (`akfamily/akshare`) | Pin it. |
| baostock | Active (recent PyPI activity) | Slower-moving; pin it. |
| mootdx | Active (preferred over the now-unmaintained `pytdx`) | Pin it. |
| edgartools | **Very active** — v5.35.0 (2026-06-02), MIT; 13F + Form 3/4/5 + N-PORT | Healthy; pin it. |
| fredapi / httpx | Active | Pin them. |
| DeepSeek | **V4 Preview released ~2026-04-24** (1M context, two models, API migration notes) | Chore-model only per CLAUDE.md → P2-4. Endpoint/model-name migration to verify if/when wired. |

---

## 4. P0 — must fix before trusting an unattended night

### P0-1 · A `claude_code` seat failure crashes the entire nightly run, with no trace — DIVERGENT (violates "invalid degrades, never raises")

**Spec/invariant:** invalid inputs must degrade to a kept-but-inert state, never raise; the orchestrator must abstain when blind. **Evidence (verified end-to-end):**
- `factor_scope/digest/claude_code.py:79-87` — `subprocess.run(..., check=True, timeout=...)` raises `CalledProcessError` (missing `claude` binary or non-zero exit), `TimeoutExpired`, or `json.JSONDecodeError` (malformed model output). `_complete` has **no** try/except.
- `factor_scope/digest/orchestrator.py:260-265` — `provider.argue(...)` / `provider.synthesize(...)` are bare calls.
- `factor_scope/pipeline.py:250` — `digest_item(provider, brief)` inside the per-item loop is bare; the only `try` on the path (`pipeline.py:86`) is a store/graph `finally` cleanup, not a catch.
- `factor_scope/pipeline.py:477` — `nightly()` calls `run()` with no guard; the `append_run_log(...)` at `pipeline.py:487` **never executes** when the digest raises.

**Impact:** the first item whose seat call fails (a transient timeout, a `claude` upgrade, one malformed JSON) aborts the whole night — *and* leaves **no run-log record**, so a crashed night is invisible. This is the highest-stakes gap: it defeats both the degrade invariant and run observability at once.

**Remediation:** wrap each seat/synthesis call so a failure **degrades that item to an explicit `abstain`** (mirroring `FactorState(valid=False)` and the existing abstain-when-blind path) and is **recorded in the run log** with the error; the run completes and emits a valid (sparser) artifact. Add unit tests for each failure mode (missing binary, timeout, non-zero exit, malformed JSON) asserting abstain + a logged failure, never a raise.

---

## 5. P1 — spec divergences & incomplete layers

### P1-1 · Connection graph is a DuckDB edge-table, not a Cypher graph DB — DIVERGENT (§ connection graph)
Spec mandates a graph-native store with Cypher (Neo4j Community / **LadybugDB** / FalkorDB) and explicitly rejects in-memory/NetworkX. Repo uses an append-only **DuckDB `edges` table** (`graph/store.py:54-63`) with hand-written `QUALIFY` look-through. The docstring concedes "a graph-native Kùzu / Neo4j is the production swap" — but **Kùzu was archived Oct 2025**.
**Remediation:** adopt **LadybugDB** behind the existing `GraphStore` Protocol — it is spec-named, embedded (fits local-first), speaks openCypher, interoperates with DuckDB/Arrow, and is the maintained Kùzu lineage. The Protocol seam (`graph/store.py:37-51`) already makes this a swap, not a rewrite.

### P1-2 · Graph edges are not idempotent — AT-RISK
`_SCHEMA` (`graph/store.py:54-63`) has **no PRIMARY KEY**; `add_edges` (`:80-85`) is a bare `INSERT ... VALUES` with **no `ON CONFLICT`**. Re-running `ingest` for the same night **duplicates every edge** (the sibling readings store correctly uses a PK + `ON CONFLICT DO NOTHING`). Look-through weights silently inflate on re-run.
**Remediation:** add a natural PRIMARY KEY (`fund, security, rel, as_of, source`) + `ON CONFLICT DO NOTHING`, matching the readings store. *Do this regardless of the P1-1 backend decision.*

### P1-3 · Live extras are unpinned — AT-RISK
`pyproject.toml:14-21` lists `akshare, baostock, edgartools, fredapi, httpx, mootdx` with **no version constraints**. A `uv sync --extra live` on a fresh machine can resolve to incompatible majors (edgartools alone moved to v5.35.0). Reproducibility of the live path is unguaranteed.
**Remediation:** pin compatible lower/upper bounds for each live dep and ensure `uv.lock` is committed and used by the nightly. (All are actively maintained — pin, don't replace.)

### P1-4 · `--output-format json` instead of `stream-json` — DIVERGENT (§ digestion)
`claude_code.py:76` uses `--output-format json`; the spec names `stream-json`.
**Remediation:** move to `--output-format stream-json` to match the spec, **preserving capture of the cost/usage envelope** (feeds P2-1). Note the tension: the non-streaming `json` envelope is the convenient carrier for `total_cost_usd`; ensure the streaming switch keeps cost visibility.

### P1-5 · Bull/bear argued sequentially, not in parallel via the Task tool — DIVERGENT (§ digestion)
`orchestrator.py:260-261` calls `argue(BULL)` then `argue(BEAR)` as two sequential subprocesses; the spec calls for parallel bull/bear via the Task tool.
**Remediation:** dispatch the two seats concurrently (and per spec, via the Task-tool pattern). Halves wall-clock per item and matches the named mechanism.

### P1-6 · Seat brief omits the fetched evidence — PARTIAL (§ digestion)
`_brief_prompt` (`claude_code.py:105-122`) renders factor states + connections + weak patterns but **never reads `brief.evidence` or `brief.as_of`**, though both are populated on `DigestInput` (`pipeline.py:247-248`). The real seats argue without the dated evidence the spec requires them to see.
**Remediation:** include `brief.evidence` (dated reads) and `brief.as_of` in the rendered prompt.

### P1-7 · 4 of 8 factor states are stubs — PARTIAL (§ factor battery)
`factors/battery.py`: `cross_market`, `crowding`, `demand`, `valuation` return `_unavailable()` (`valid=False`). Only trend-gate, reversal, low-vol, and macro dial compute. Half the battery is inert.
**Remediation:** implement the four stubbed states against their spec definitions (each still a pure `(FactorContext) -> FactorState`, ranked against its own history — no composite).

### P1-8 · `reversal` is a bare return-percentile — PARTIAL (§ factor battery)
`reversal` computes a 20-day return percentile only; the spec's reversal/exhaustion reading also weighs **turnover / Amihud illiquidity**.
**Remediation:** add the turnover/Amihud inputs to the reversal state.

### P1-9 · AkShare endpoint substitutions — DIVERGENT (§ ingest)
`ingest/prices.py` uses `fund_etf_hist_em`; `ingest/fund_holdings.py` uses `fund_portfolio_hold_em`. Under strict-literal basis these differ from the spec-named surfaces.
**Remediation:** reconcile against the spec-named endpoints (or record a justified, dated deviation if the named endpoint is gone).

### P1-10 · BERTopic signal-strength engine absent — MISSING (§ emerging, Stage A)
BERTopic is referenced only in a docstring (`ingest/themes.py:6`) and never implemented; Stage-A `signal_strength()` runs on hand-fed theme fields.
**Remediation:** implement the BERTopic-based signal-strength stage, or record a deliberate deviation with the substitute.

### P1-11 · Missing ingest / graph surfaces — MISSING (§ ingest / connection graph / emerging)
No AkTools, no messy-web/crowding source, no lead-chain extraction. These feed the crowding factor, the `EXPOSED_TO` driver/theme edges, and the emerging lead-chain test — all currently un-sourced.
**Remediation:** scope and add these ingest surfaces (each as an additive pipeline step so a partial pipeline still emits a valid artifact).

### P1-12 · Broken documentation references — AT-RISK
`README.md:119` and `CLAUDE.md:22,61` point to `docs/ARCHITECTURE.md` and `docs/spec/SPEC.md`, **both deleted** in commit `3680955`. New contributors (and agents) are sent to dead paths.
**Remediation:** repoint to live docs (this file, `docs/ops/RUNBOOK.md`, the v4 HTML spec) or restore the architecture/spec docs.

---

## 6. What is CONFORMANT (verified — do not regress)

These are genuinely met and test-enforced; they are the spine the remediations hang off:

- **Determinism** — fixtures runs derive `generated_at` from `as_of`; artifact reproduces byte-for-byte (system tests).
- **Append-only store** — DuckDB readings log with PK + `ON CONFLICT DO NOTHING`; `read_as_of` is point-in-time (`test_store_pit`).
- **Trend-gate hard cap** — below 200-day MA → `capped`; the orchestrator enforces it on top of model output (`test_gate_enforced`).
- **States, not a composite** — each factor is a pure `(FactorContext) -> FactorState`; cut-points (`bands.py`) are fixed constants, never tuned to P&L.
- **Abstain-when-blind + scorecard guardrails** — `MIN_VALID_STATES`, opposing-extremes downgrade, scorecard nudge — all test-covered (`test_abstain`, `test_guardrails`, `test_auto_downgrade`).
- **Dashboard contract** — pydantic models + JSON-schema export; descriptive models frozen (one exception, P2-2).
- **Offline by default** — core install + full suite + CI run offline on the `fake` provider; heavy/network deps imported lazily inside the call (`claude_code._complete`, `DuckDBGraphStore.__init__`).
- **FRED / EDGAR / positions ingest** — spec-conformant (FRED `DEFAULT_SERIES`, EDGAR 13F + N-PORT, exact positions schema).
- **Nightly scheduling + ops** — launchd one-shot + cron line; `docs/ops/RUNBOOK.md` is accurate and honest about the deferred "graduate" tier.

---

## 7. P2 — production hardening & hygiene

- **P2-1 · Nightly cost telemetry + $20/mo budget guard (AT-RISK).** Capture `total_cost_usd` / token usage from the `claude -p` envelope into the run log per night; warn as the monthly Pro Agent-SDK credit ($20, no rollover) nears exhaustion. *Directly serves the Pro-plan-quota goal (see §2).*
- **P2-2 · Freeze the `Scorecard` model (AT-RISK).** `contract/__init__.py:98-110` is the only descriptive model not `frozen=True`; freeze it for consistency/safety.
- **P2-3 · Temporal edge fields (PARTIAL, § connection graph).** `Edge` carries `as_of` only; add `valid_from` / `valid_to` for true bitemporal look-through.
- **P2-4 · Wire or remove DeepSeek (AT-RISK).** Declared chore model, wired to nothing. If kept, account for the V4-preview model/endpoint migration; else drop the reference.
- **P2-5 · CI should call `make check` (AT-RISK).** `.github/workflows/ci.yml` re-implements ruff+mypy+pytest inline instead of invoking the one gate — drift risk between local and CI.
- **P2-6 · `Theme.base_level` dead field (AT-RISK).** Populated (`pipeline.py:280`) but never read by `signal_strength()`; wire it in or remove it.
- **P2-7 · Graduate-tier robustness (MISSING by design).** DSR / PBO / CPCV / ArcticDB are documented-not-built in the RUNBOOK. Track as a known deferred tier; not for now.

---

## 8. Recommended sequencing

1. **P0-1 first** — until the digest path is crash-safe and failures are logged, no unattended night can be trusted.
2. **P1-2 (idempotency) + P1-3 (pin deps) + P1-12 (doc refs)** — cheap, high-leverage data-integrity / reproducibility fixes.
3. **P2-1 (cost telemetry)** — needed before June 15 to manage the $20 ceiling.
4. **P1-1 (LadybugDB), P1-4/5/6 (digest conformance)** — the substantive spec-conformance work.
5. **P1-7/8/9/10/11** — fill out the factor battery, ingest, and emerging surfaces.
6. **P2 remainder** — hygiene as capacity allows.
