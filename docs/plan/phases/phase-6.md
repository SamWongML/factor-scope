# Phase 6 — Emerging radar funnel (L3)  ·  STATUS: done

Spec: §07. Two-stage funnel: qualify the **industry**, then screen its **funds** to a top 3.

## Goal
Populate the `emerging` list with a defensible **top-3** funds for each qualified theme, with overlap
measured against the existing book via the Phase-3 look-through.

## Design
- `factor_scope/emerging/`:
  - `stage_a.py` — qualify the industry: signal strength (acceleration + breadth across distinct
    sources − crowding penalty), durability (broad adoption + path to profitability + fad-resistance),
    lead-chain corroboration (US relay confirms real end-demand), investable-wrapper exists. A theme
    must clear A before any fund is considered. Clustering via provider tagging (DeepSeek/BERTopic).
  - `stage_b.py` — for a cleared theme, score candidate CN funds/ETFs on a fixed scorecard:
    index methodology/pure-play, **overlap-with-core (via §05)**, crowding (§03), cost, liquidity/size,
    tracking quality, concentration → ranked **top 3** with a one-page comparison.

## TDD plan
- `tests/unit/test_stage_a.py`: a strong-but-durable theme passes; a fad / no-wrapper theme stops.
- `tests/unit/test_stage_b.py`: deterministic ranking on fixture funds; high overlap shrinks/drops a fund.
- Reuse the Phase-3 look-through for overlap (no new graph logic).

## System test
Fixtures → `emerging` list holds a top-3 with methodology/fee/AUM/tracking/crowding + overlap;
artifact valid + deterministic.

## Done when
Stage-A gate + Stage-B rank deterministic; overlap via §05; `make system` green; `PROGRESS.md` + commit.
