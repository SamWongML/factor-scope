"""The nightly pipeline — orchestrates the layers into one ``dashboard.json``.

Phase 0 is intentionally a thin spine: it reads the bundled fixture item list and produces a
schema-valid (near-empty) :class:`~factor_scope.contract.Dashboard`. Later phases enrich each
item in place — ingestion/evidence (P1), factor states + gate (P2), connections (P3), scorecard
(P4), the calibrated lean (P5), the emerging list (P6) — without changing this entrypoint's
contract. ``run --fixtures`` therefore stays runnable at every phase boundary.
"""

from __future__ import annotations

import json
from typing import Any

from factor_scope.config import Config
from factor_scope.contract import Dashboard, DashboardItem, Evidence, ListName


def _load_fixture(config: Config) -> dict[str, Any]:
    path = config.fixtures_dir / "items.json"
    if not path.exists():
        raise FileNotFoundError(
            f"fixture not found: {path}. Run from the repo root, or pass --fixtures-dir."
        )
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def build_dashboard(config: Config) -> Dashboard:
    """Build the morning artifact for one run.

    Determinism: the as-of date is taken from config, else from the fixture stamp — never from
    the wall clock — so a fixtures run reproduces byte-for-byte.
    """

    if config.source != "fixtures":
        # Live ingestion lands in Phase 1; until then only the offline path exists.
        raise NotImplementedError(
            f"source={config.source!r} is not wired yet; use the default 'fixtures' source."
        )

    raw = _load_fixture(config)
    as_of = config.as_of or raw["as_of"]

    items: list[DashboardItem] = []
    for entry in raw.get("items", []):
        items.append(
            DashboardItem(
                item=entry["item"],
                list=ListName(entry["list"]),
                evidence=[Evidence(**ev) for ev in entry.get("evidence", [])],
            )
        )

    # A fixtures run is deterministic: the timestamp is derived from the as-of stamp, not the
    # wall clock, so the artifact reproduces byte-for-byte (golden-file friendly).
    return Dashboard(
        as_of=as_of,
        generated_at=f"{as_of}T22:00:00Z",
        items=items,
    )


def run(config: Config) -> Dashboard:
    """Build the dashboard and persist it to ``config.output_path``."""

    dash = build_dashboard(config)
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    config.output_path.write_text(dash.model_dump_json(indent=2), encoding="utf-8")
    return dash
