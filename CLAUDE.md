# factor-scope

Local-first, single-user **nightly-batch** decision-support engine for an A-share / funds-and-ETFs
portfolio. One run emits one dated artifact, **`out/dashboard.json`** (the contract everything
reads/writes), reviewed each morning. **It never places orders.** Educational tooling, not advice.

## Commands

`uv` is the package manager — don't call `pip`/`python` directly; go through `uv run` (Makefile does).

```
make setup    # uv venv + install (.[dev,store])
make run      # build dashboard.json from bundled fixtures and print it
make test     # full offline suite (unit + integration + system)
make unit     # fast pure-function tests only
make system   # end-to-end entrypoint run — the "nothing is broken" gate
make check    # lint + typecheck + test — THE bar; a change isn't done until this is green
```

CLI: `factor-scope run|nightly|ingest|schedule|schema` (`--help` per command).

## Layout — full map in `docs/ROADMAP.md`

- `factor_scope/contract/` — pydantic models for `dashboard.json` + JSON-schema export. **The spine.**
- `pipeline.py` — `ingest()` fills the store; `build_dashboard`/`run` read it → `Dashboard`.
- `ingest/ store/ factors/ graph/ scoring/ digest/` — the six layers, plus `emerging/`; each is a
  pipeline step that only **adds** to the artifact, so a partial pipeline still emits a valid one.
- `cli.py` typer app · `render.py` terminal view · `schedule/` launchd+cron deploy.
- `data/fixtures/` committed sample data · `tests/{unit,integration,system}`.

## How to work here

- Test-first: RED → GREEN → REFACTOR. Mark every test `unit` / `integration` / `system` (`pyproject.toml`).
- Keep `make test` and `make system` green at every boundary; the entrypoint stays runnable.
- mypy is **strict**, ruff line-length 100, and deprecation warnings are errors in the suite — don't
  restate these, just run `make check`.

## Hard rules (costly to rediscover)

- **States, not a composite.** A factor is a descriptive `(FactorContext) -> FactorState` — a band +
  risk direction + dated evidence, each ranked against its *own* history. **Never** form a
  weighted/fitted composite or tune cut-points to P&L.
- **Determinism.** Fixtures runs derive `generated_at` from `as_of` — **no wall clock** in the
  artifact path; `dashboard.json` must reproduce byte-for-byte. (Ops telemetry like the run log may
  use real timestamps; the artifact may not.)
- **The store is append-only.** Every fact is a `Reading` in a DuckDB log — no update/delete, so a
  later disclosure never rewrites an earlier read. Reasoning sees only what was knowable as-of the run date.
- **The trend gate is the one hard cap.** Below the 200-day MA → `capped`, and **nothing may open a
  capped gate.** Guardrails (gate, abstain-when-blind, scorecard) are enforced by the orchestrator
  *on top of* model output — even an overconfident model can't open the gate.
- **Invalid inputs degrade, never raise.** A stale/short/missing reading → `FactorState(valid=False)`;
  it's kept, not dropped.
- **Online by default; offline is the test mode.** `run`/`nightly` default to live data sources +
  the real provider; **offline** — fixtures + the **`fake`** provider — is opted into with `--offline`
  or `FACTOR_SCOPE_OFFLINE=1`. The whole suite + CI force offline (`tests/conftest.py`) and stay
  byte-for-byte deterministic — preserved by the snapshot boundary + mocks, not by avoiding
  the network. `live`/`store` deps are **pinned** extras imported **lazily inside the call** (see
  `digest/claude_code.py`) so the offline path never shells out.
- **Providers:** `claude_code` (default — real bull/bear→synthesis via headless `claude -p`, agents
  in `.claude/agents/`) · `fake` (the deterministic offline stub) · DeepSeek is a **chore** model
  only, never a `--provider` value.

Ops (nightly job, scheduling, provider budget): `docs/ops/RUNBOOK.md`. Direction, architecture map &
upgrade plan: `docs/ROADMAP.md`.
