# Phase 3 — Connection graph + deterministic look-through (L2)  ·  STATUS: done

Spec: §05. Graph engine decided — see DECISIONS.md **D8** (embedded on-disk graph in DuckDB behind a
`GraphStore` interface; Kùzu / Neo4j Community the documented production swap).

## Goal
Build a durable, on-disk, **temporal** graph from holdings feeds and answer the motivating question
exactly: "B is falling — who else of mine holds it, and my total look-through weight?" → fill
`connections[]` + set `connections_flag`.

## Design
- `factor_scope/graph/` with a `GraphStore` interface (so the engine is swappable):
  - default: embedded on-disk engine (Kùzu/LadybugDB) — pick after a maturity check; Neo4j Community
    documented as the production swap. **No NetworkX / in-memory rebuild.**
- Schema: `(:Fund)-[:HOLDS {weight, as_of}]->(:Security)-[:EXPOSED_TO]->(:Driver|:Theme)`. Hard edges
  (HOLDS, EXPOSED_TO) come straight from the §04 holdings feeds — no LLM. Every edge carries
  `{as_of, source, valid_from, valid_to}` → point-in-time, quarterly snapshots.
- Look-through query: for a falling security, return the funds on my lists holding it and
  `sum(weight * my_position)` as total look-through. Exact set arithmetic, auditable, instant.
- Same query powers Phase 6 overlap-with-core and makes the §03 crowding state concrete for my book.

## TDD plan
- `tests/unit/test_lookthrough.py`: a falling fixture name → exact set of funds + correct weight;
  point-in-time (an earlier snapshot doesn't see a later disclosure).
- `tests/integration/test_graph_store.py`: build → persist → reload → query (durability).

## System test
A fixture security marked down surfaces in the relevant items' `connections[]` with the right
`lookthrough_wt`; `connections_flag` set; artifact valid + deterministic.

## Done when
Look-through exact + point-in-time; graph persists on disk; `make system` green; graph-engine choice
in `DECISIONS.md`; `PROGRESS.md` + commit.
