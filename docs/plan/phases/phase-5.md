# Phase 5 — Digestion: LLM provider + bull/bear → synthesis (L4)  ·  STATUS: todo

Spec: §08. Decisions: D2 (fake by default).

## Goal
Turn states + connections + scorecard + evidence into a **calibrated lean** via a two-sided debate,
with the gate enforced and abstain-when-blind. CI runs entirely on the deterministic fake provider.

## Design
- `factor_scope/digest/`:
  - `provider.py` — `LLMProvider` interface (`complete`, structured output). Impls:
    - `fake.py` (**default**) — deterministic rules over the inputs producing a valid `Lean` +
      evolution + flip_trigger + invalidation. No network. Used by all tests.
    - `claude_code.py` — headless `claude -p ... --output-format stream-json`; bull/bear as subagents
      defined in `.claude/agents/` (isolated contexts) spawned via the Task tool.
    - `deepseek.py` — DeepSeek V4 for cheap chores (reformat/summarise evidence). Off the judgement path.
  - `orchestrator.py` — run bull + bear (isolated) → synthesis: anchor a base rate, weigh both cases,
    read the scorecard (lower confidence where a state-pattern was overconfident), note connections,
    **enforce the gate** (capped → lean ≤ Hold/Avoid, no exceptions), abstain if blind. Emit the lean.
  - Prompts: the synthesis prompt from spec §08; bull and bear system prompts in `.claude/agents/`.

## TDD plan
- `tests/unit/test_fake_provider.py`: deterministic lean for given inputs.
- `tests/unit/test_gate_enforced.py`: capped gate caps the lean regardless of bullish states; no
  scorecard note can open it.
- `tests/unit/test_abstain.py`: too many invalid states / opposing extremes → `abstain`.
- `tests/unit/test_scorecard_nudge.py`: overconfident pattern lowers confidence.
- Contract test the provider interface so real impls can be dropped in.

## System test
End-to-end `run --provider fake` → every item gets a calibrated `lean` + `evolution` +
`flip_trigger` + `invalidation`; gate respected; deterministic. Real providers exercised via opt-in
(e.g. `FACTOR_SCOPE_LLM=claude_code`), never in CI.

## Done when
Fake-provider pipeline deterministic; gate + abstain + scorecard-nudge covered; real providers wired +
documented; `make system` green; `PROGRESS.md` + commit.
