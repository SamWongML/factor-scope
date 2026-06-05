# ROADMAP — phases

Each phase is built test-first (RED → GREEN → REFACTOR), ends with a green `make test` + `make
system`, and leaves `factor-scope run` runnable so a human can review the current output. The
system is **never broken between phases**. Detailed task breakdowns live in `phases/phase-N.md`
(load only the current one). Live state is in `PROGRESS.md`.

| # | Phase | Spec | Adds to dashboard.json | Exit gate |
|---|-------|------|------------------------|-----------|
| 0 | Scaffold, contract, entrypoint, tracking | §02, §11 | the artifact + lists (from fixtures) | `make system` green; docs scaffold exists |
| 1 | Ingestion + point-in-time store (L1+L2) | §04, §09 | items + `evidence[]` from the store | ingest→store→run populates lists; append-only enforced |
| 2 | Factor states + trend gate (L3 core) | §03 | `states[]` + `gate` | 8 states deterministic; gate caps lean |
| 3 | Connection graph + look-through (L2) | §05 | `connections[]` + `connections_flag` | falling name → exact overlap + weight |
| 4 | Self-scoring loop (L3) | §06 | `scorecard` | Brier/BSS correct; resolved call immutable; guardrails hold |
| 5 | Digestion: LLM provider + bull/bear (L4) | §08 | `lean` + `evolution` + `flip_trigger` + `invalidation` | fake provider deterministic; gate enforced; abstain path |
| 6 | Emerging radar funnel (L3) | §07 | `emerging` list = top-3 | Stage-A gate + Stage-B rank deterministic; overlap via §05 |
| 7 | Scheduling, packaging & ops | §11 | (ops) | nightly run e2e on fixtures; launchd plist + docs |

**Graduate tier (documented, not built):** ArcticDB bitemporal backtesting under DSR/PBO/CPCV; an
optional local vector store. Add only when backtesting begins.

## Per-phase definition of done
- New unit + integration tests written first and green; **no test weakened to pass**.
- `make system` (the e2e `factor-scope run --fixtures` gate) green, asserting the phase's new block.
- `make lint` + `make typecheck` clean.
- `factor-scope run` prints a reviewable artifact; determinism (golden) holds.
- `PROGRESS.md` updated (status + NEXT ACTION); `DECISIONS.md` updated if a choice was made; commit.
