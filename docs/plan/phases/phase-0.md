# Phase 0 — Scaffold, contract, entrypoint, tracking  ·  STATUS: done

## Goal
Stand up the project, the `dashboard.json` **contract**, the single `factor-scope run` entrypoint,
and the cross-session tracking docs — so every later phase has a stable spine and a runnable demo.

## Delivered (test-first)
- Tooling: `pyproject.toml` (uv + hatchling, optional extras `live`/`store`/`dev`), `Makefile`,
  pytest markers (`unit`/`integration`/`system`), ruff, mypy strict, GitHub Actions CI.
- `factor_scope/contract` — pydantic models for the whole artifact + `dashboard_json_schema()`.
  Defaults keep an under-construction item valid.
- `factor_scope/pipeline.py` (spine), `render.py` (terminal L6), `cli.py` (`run`, `schema`).
- `data/fixtures/items.json` — tiny sample of the three lists.
- Tests: `tests/unit/test_contract.py`, `tests/system/test_run_smoke.py` (schema-valid + deterministic).
- Docs: `CLAUDE.md`, `docs/spec/{SPEC.md, wealth-assistant-engine-v4.html}`, `docs/ARCHITECTURE.md`,
  `docs/plan/{ROADMAP,PROGRESS,DECISIONS}.md`, `docs/plan/phases/`.

## Exit gate — met
`make test` (7 passed), `make lint`, `make typecheck` green; `make run` prints the artifact and
writes `out/dashboard.json`; fixtures run is byte-for-byte deterministic.
