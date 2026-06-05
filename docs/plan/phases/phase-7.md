# Phase 7 — Scheduling, packaging & ops  ·  STATUS: todo

Spec: §11. Decision: D4 (cross-platform core + launchd documented).

## Goal
Make the nightly run operable: a one-shot orchestration command, a thin scheduling adapter, the
macOS **launchd** production path documented, and a clean quickstart.

## Design
- `factor-scope nightly` (or `run` with `--persist-calls`) — the one-shot job: ingest → compute →
  digest → write `dashboard.json`, log a run record, and append calls for self-scoring.
- `factor_scope/schedule/` — thin adapter: emit a **launchd** plist (Mac-mini production, one-shot via
  `claude -p`, not a daemon) and document a **cron** alternative for Linux. No platform code on the
  critical path.
- Ops: structured run log (start/end, items, abstains, provider, cost note); a README "nightly setup".
- Budget note: from 15 Jun 2026 `claude -p` meters against a separate Agent-SDK credit — document
  sizing the nightly run.
- **Graduate tier (document only, do not build):** ArcticDB bitemporal backtesting under DSR / PBO /
  CPCV + parameter-stability; optional local vector store (LanceDB + local embeddings).

## TDD plan
- `tests/unit/test_schedule_render.py`: launchd plist + cron line render correctly from config.
- `tests/system/test_nightly.py`: full nightly run over fixtures end-to-end; run log written;
  calls appended for the next day's scoring.

## System test
`factor-scope nightly --fixtures` runs the whole pipeline e2e and writes a reviewable artifact + log;
scheduled-invocation smoke passes.

## Done when
Nightly e2e green; launchd plist + setup doc shipped; graduate tier documented; `make system` green;
README quickstart complete; `PROGRESS.md` + commit.
