# DECISIONS (ADR-lite)

One line per decision so future sessions don't re-litigate. Newest at the bottom.

## D1 — Data: fixtures-first, live opt-in
Bundled sample data under `data/fixtures/` drives all tests and demos (offline, deterministic). Real
fetchers (AkShare/Baostock/FRED/EdgarTools) sit behind adapters and are exercised only via an opt-in
`--live` flag + separate integration tests. Keeps TDD fast and every phase runnable on Linux/CI.

## D2 — LLM: deterministic fake by default, real wired at its phase
An `LLMProvider` interface with a deterministic **fake** default lets the full pipeline run end-to-end
with no API keys and no paid calls in tests. Real **Claude Code headless** + **DeepSeek V4** are wired
at Phase 5, selected by config (`--provider`). CI never calls real providers.

## D3 — Entrypoint: one stable CLI, never broken between phases
`factor-scope run` is the single human-facing entrypoint. Its contract (emit schema-valid
`dashboard.json` + terminal render) holds at every phase boundary; later phases only enrich the
artifact. This is the "between two phases the system is not broken" requirement.

## D4 — Platform: cross-platform core + launchd documented
The engine is a plain CLI that runs on Linux/CI and macOS. Scheduling sits behind a thin adapter; a
**launchd** plist ships in Phase 7 as the documented Mac-mini production path (cron noted for Linux).

## D5 — Package manager: uv
Use `uv` for the venv and installs (`uv venv`, `uv pip install -e ".[dev]"`, `uv run ...`). The
Makefile and CI use uv. (Confirmed by the user.)

## D6 — Build backend: hatchling
`pyproject.toml` uses hatchling with an explicit `packages = ["factor_scope"]` so the editable
install works without guessing the layout.

## Open (decide when reached)
- **Graph engine (Phase 3):** embedded on-disk default (Kùzu/LadybugDB) behind a `GraphStore`
  interface, vs defaulting straight to Neo4j Community. The interface keeps it swappable; pick at
  Phase 3 start after a quick maturity check.
- **Optional static-HTML view of `dashboard.json`** (matching the source design) — deferred; the
  stable contract is the JSON, so it can be added later without disruption.
