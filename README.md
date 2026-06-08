# factor-scope

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://docs.python.org/3/)
[![uv](https://img.shields.io/badge/package_manager-uv-orange)](https://github.com/astral-sh/uv)

**factor-scope** is a local-first, nightly-batch decision-support tool for an A-share / funds-and-ETFs portfolio. Each run writes one dated artifact, `out/dashboard.json`, reviewed each morning.

> It never places orders. You remain the only thing that clicks buy.
> Not financial advice. Educational research tooling.

---

## Quickstart

```bash
make setup      # uv venv + install (.[dev,store])
make run        # build dashboard.json from bundled fixtures and print it
make test       # full offline suite (unit + integration + system)
make check      # lint + typecheck + test — the CI bar
```

`make run` writes `out/dashboard.json` and prints a terminal summary. Everything runs **offline on bundled fixtures** by default; live data and real LLM providers are opt-in extras.

---

## How it works

The engine runs six layers, each adding to a single artifact without replacing prior work. A partial pipeline still emits a valid (sparser) dashboard.

| Layer | Module | What it does |
|-------|--------|--------------|
| Ingest | `ingest/` | Pull CN prices/holdings + US FRED/EDGAR + `positions.csv` |
| Store | `store/` | Append every reading into a point-in-time DuckDB log |
| Factors | `factors/` | Compute 8 descriptive factor states + the 200-day trend gate |
| Graph | `graph/` | Build the holdings graph; deterministic fund look-through |
| Digest | `digest/` | Bull/bear debate → calibrated lean (fake \| claude_code) |
| Scoring | `scoring/` | Rolling Brier scorecard — self-scoring mirror |
| +Emerging | `emerging/` | Stage-A screen → Stage-B top-3 to watch |

Data flow: `cli.run` → `Config` → `pipeline.run` → `dashboard.json` → terminal render.

---

## Output: `dashboard.json`

One file, one schema version, three lists (`holdings · watchlist · emerging`).

```jsonc
{
  "schema_version": 1,
  "as_of": "2026-06-05",
  "generated_at": "2026-06-05T22:00:00Z",
  "items": [
    {
      "item": "通信ETF",
      "list": "holdings",
      "gain": -0.018,
      "gate": "open",
      "states": [
        { "factor": "trend gate",   "level": "extreme_high", "direction": "uptrend (+10% vs 200d-MA)", "valid": true },
        { "factor": "reversal",     "level": "low",          "direction": "soft run → reversal-UP potential", "valid": true },
        { "factor": "macro dial",   "level": "extreme_high", "direction": "tight (real-yield headwind)", "valid": true }
      ],
      "lean": { "action": "hold", "confidence": 0.6, "text": "Hold / moderate-conviction" },
      "evolution": "Abstain→Hold",
      "connections": [{ "shared": "中际旭创 ↓", "also_in": ["光通信 / optical-module ETF"], "lookthrough_wt": 0.043 }]
    }
  ]
}
```

Print the full JSON schema with `factor-scope schema`.

---

## Design invariants

- **States, not a composite.** Each factor is a pure `(FactorContext) → FactorState` — a percentile band + risk direction + dated evidence, ranked against its *own* history. No weighted/fitted composite, no P&L-tuned cut-points.
- **The trend gate is the one hard cap.** Price below 200-day MA → `capped`; lean is capped at Hold/Avoid. The orchestrator enforces this on top of model output — an overconfident model cannot open a capped gate.
- **Append-only store.** Every fact is a `Reading` in DuckDB — no update/delete. A later disclosure never rewrites an earlier read. `read_as_of(series, D)` returns only what was knowable that day.
- **Determinism.** Fixtures runs derive `generated_at` from `as_of` — no wall clock in the artifact path. `dashboard.json` reproduces byte-for-byte.
- **Invalid inputs degrade, never raise.** Stale or missing readings produce `FactorState(valid=False)`, kept in the artifact but not acted on.
- **Offline by default.** Core install + full test suite run offline. Live data sources and real LLM providers are opt-in; their deps are imported lazily so the fixtures path never shells out.

---

## CLI reference

```
factor-scope run      # build dashboard.json (--live to use live sources, --provider claude_code)
factor-scope nightly  # production one-shot: ingest → compute → digest → artifact + run log
factor-scope ingest   # fill the durable store only (separates fetch from reason)
factor-scope schedule # emit launchd plist (macOS) or cron line (Linux) for the nightly job
factor-scope schema   # print the dashboard.json JSON schema
```

### Nightly scheduling

```bash
factor-scope nightly                                              # run once now
factor-scope schedule -o ~/Library/LaunchAgents/com.factor-scope.nightly.plist  # macOS
factor-scope schedule --kind cron --working-dir "$PWD"           # Linux: prints a crontab line
```

Ops guide (install/enable, run log, provider budget): `docs/ops/RUNBOOK.md`.

---

## Development

```bash
make unit       # fast pure-function tests only
make system     # end-to-end entrypoint smoke test
make check      # lint (ruff) + typecheck (mypy strict) + full suite — the gate
```

Tests are marked `unit` / `integration` / `system` (see `pyproject.toml`). Work test-first: RED → GREEN → REFACTOR. The entrypoint must always stay runnable.

Architecture + contract: `docs/GAP-ANALYSIS.md`. Full spec: `docs/spec/wealth-assistant-engine-v4.html`. Agent instructions: `CLAUDE.md`.
