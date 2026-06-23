# factor-scope

The build produces one dated artifact, **`out/dashboard.json`** — the contract every layer
reads/writes. Each pipeline step only **adds** to it, so a partial pipeline still emits a valid one.

## Commands

`uv` is the package manager — never call `pip`/`python` directly; go through `uv run` (the Makefile does).

```
make setup   # uv sync --frozen --extra dev --extra store --extra serve --extra live
make run      # factor-scope run --offline — build dashboard.json from fixtures and print it
make test     # full offline suite (unit + integration + system)
make unit     # fast pure-function tests only
make system   # end-to-end entrypoint run — the "nothing is broken" gate
make check    # lint + typecheck + test — THE bar; a change isn't done until this is green
```

CLI: `factor-scope run|nightly|discover|ingest|serve|schedule|schema` (`--help` per command).

## Layout

- `contract/` — pydantic models for `dashboard.json` + JSON-schema export. **The spine.**
- `pipeline.py` — `ingest()` fills the store + holdings graph; `build_dashboard`/`run` read a frozen
  snapshot → `Dashboard`; `nightly()` is the full job (ingest → build → log → persist).
- `ingest/ store/ factors/ graph/ scoring/ digest/` (+ `emerging/`) — the pipeline layers;
  `discovery/` is a separate user/cron-triggered theme-research service, **not** the nightly.
- `cli.py` typer app · `render.py` terminal view · `series.py` per-fund trails · `schedule/` launchd+cron.
- `history.py` — every run also lands as an immutable `out/dashboards/<as_of>.json` (first write wins;
  a later run never rewrites a night) · `serve.py` — read-only history API (`serve` extra).
- `data/fixtures/` committed sample data · `tests/{unit,integration,system}`.

## How to work here

- Test-first: RED → GREEN → REFACTOR. Mark every test `unit`/`integration`/`system` (markers in `pyproject.toml`).
- Keep `make test` and `make system` green at every boundary; the entrypoint stays runnable.
- mypy is **strict**, ruff line-length 100, deprecation warnings are errors — don't restate, just run `make check`.
- **Name for the domain, not the process.** Keep scaffolding terms — `issue`, `phase`, `step`/`stepN`,
  `task`, `ticket`, `TODO`, `tag`, `WIP`, `v2`/`new`/`old`, AI/agent artifacts — out of identifiers,
  filenames, comments, and docstrings. Names say what the code *is* in factor-scope's vocabulary
  (factors, readings, gates, the artifact), not how/when it was built. No traces of implementation history.

## Hard rules (costly to rediscover)

- **States, not a composite.** A factor is a descriptive `(FactorContext) -> FactorState` — band +
  risk direction + dated evidence, each ranked against its *own* history. **Never** form a
  weighted/fitted composite or tune cut-points to P&L.
- **Determinism.** Fixtures runs derive `generated_at` from `as_of` — **no wall clock** in the artifact
  path; `dashboard.json` reproduces byte-for-byte. (Ops telemetry like the run log may use real
  timestamps; the artifact may not.)
- **The store is append-only.** Every fact is a `Reading` in a DuckDB log — no update/delete, so a
  later disclosure never rewrites an earlier read. Reads are point-in-time: reasoning sees only what
  was knowable as-of the run date.
- **Snapshot boundary.** `ingest()` fetches and writes; `run`/`build_dashboard` only **read** a frozen
  snapshot — never fetch. A live source over an empty store raises — ingest first. (Offline reads
  committed fixtures, which isn't fetching, so it runs standalone.)
- **The trend gate is the one hard cap.** Below the 200-day MA → `capped`, and **nothing may open a
  capped gate.** Guardrails (gate, abstain-when-blind, scorecard) are enforced by the orchestrator
  *on top of* model output — even an overconfident model can't open the gate.
- **Invalid inputs degrade, never raise.** A stale/short/missing reading → `FactorState(valid=False)`;
  it's kept, not dropped.
- **Online by default; offline is the test mode.** `run`/`nightly` default to live sources + the real
  provider; **offline** (fixtures + the `fake` provider) is opted into with `--offline` or
  `FACTOR_SCOPE_OFFLINE=1`. The suite + CI force offline (`tests/conftest.py`) and stay byte-for-byte
  deterministic — preserved by the snapshot boundary + mocks, not by avoiding the network.
  `live`/`store`/`serve`/`discover` deps are **pinned** extras imported **lazily inside the call**
  (see `digest/claude_code.py`) so the offline path never shells out.
- **Providers:** `claude_code` (default — real bull/bear→synthesis via headless `claude -p`, seat
  prompts in `.claude/agents/`) · `fake` (the deterministic offline stub) · DeepSeek is a **chore**
  model only, never a `--provider` value.

Ops (nightly job, scheduling, provider budget): `docs/ops/RUNBOOK.md`.
