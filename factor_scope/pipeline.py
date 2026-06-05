"""The nightly pipeline — orchestrates the layers into one ``dashboard.json``.

Phase 1 wires the point-in-time store between ingestion and the artifact: ``ingest`` fills the
store and ``build_dashboard`` reads it (point-in-time, as of the run date) to produce the three
lists with real ``evidence[]`` and a per-item ``gain`` (cost basis vs current NAV). So the
entrypoint keeps working standalone, a fixtures ``run`` against an empty store auto-ingests first.
Later phases enrich each item in place — states + gate (P2), connections (P3), scorecard (P4), the
lean (P5), the emerging list (P6) — without changing this entrypoint's contract.
"""

from __future__ import annotations

import json

from factor_scope.config import Config
from factor_scope.contract import Dashboard, DashboardItem, Evidence, ListName
from factor_scope.ingest import gather_fixture_readings, gather_live_readings
from factor_scope.store import DuckDBStore, PointInTimeStore, Reading


def _resolve_as_of(config: Config) -> str:
    """The point-in-time date the engine reasons on: the CLI override, else the fixture stamp."""

    if config.as_of:
        return config.as_of
    manifest = config.fixtures_dir / "manifest.json"
    if not manifest.exists():
        raise FileNotFoundError(
            f"no as_of given and fixture manifest not found: {manifest}. "
            "Run from the repo root, pass --as-of, or use --fixtures-dir."
        )
    data: dict[str, str] = json.loads(manifest.read_text(encoding="utf-8"))
    return data["as_of"]


def _open_store(config: Config) -> DuckDBStore:
    return DuckDBStore(":memory:" if config.store_path is None else config.store_path)


def _gather(config: Config, as_of: str) -> list[Reading]:
    if config.source == "fixtures":
        return gather_fixture_readings(config, as_of=as_of)
    return gather_live_readings(config, as_of=as_of)


def ingest(config: Config) -> int:
    """Fill the store from the configured source. Returns the number of rows appended."""

    as_of = _resolve_as_of(config)
    store = _open_store(config)
    try:
        return store.append(_gather(config, as_of))
    finally:
        store.close()


def _build_items(store: PointInTimeStore, as_of: str) -> list[DashboardItem]:
    """Project the point-in-time store into dashboard items (positions + priced evidence)."""

    prices = {r.key: r for r in store.read_as_of("prices", as_of)}
    items: list[DashboardItem] = []
    for pos in store.read_as_of("positions", as_of):
        cost_basis = float(pos.payload["cost_basis"])
        gain: float | None = None
        evidence: list[Evidence] = []
        price = prices.get(pos.key)
        if price is not None:
            nav = float(price.payload["nav"])
            if cost_basis > 0:
                gain = (nav - cost_basis) / cost_basis
            gain_text = f"{gain:+.1%}" if gain is not None else "n/a"
            one_line = (
                f"NAV {nav:g} (as of {price.as_of}); gain {gain_text} vs cost {cost_basis:g}"
            )
            evidence.append(
                Evidence(src="akshare:fund_etf_hist", as_of=price.as_of, one_line=one_line)
            )
        items.append(
            DashboardItem(
                item=str(pos.payload["name"]),
                list=ListName(pos.payload["list"]),
                gain=gain,
                evidence=evidence,
            )
        )
    return items


def build_dashboard(config: Config) -> Dashboard:
    """Build the morning artifact for one run from the point-in-time store.

    Determinism: the as-of date is the override or the fixture stamp (never the wall clock), and a
    fixtures ingest is stamped deterministically, so a fixtures run reproduces byte-for-byte.
    """

    as_of = _resolve_as_of(config)
    store = _open_store(config)
    try:
        if store.count("positions") == 0:
            # Empty store → auto-ingest so `run` works without a separate `ingest` step.
            store.append(_gather(config, as_of))
        items = _build_items(store, as_of)
    finally:
        store.close()

    return Dashboard(as_of=as_of, generated_at=f"{as_of}T22:00:00Z", items=items)


def run(config: Config) -> Dashboard:
    """Build the dashboard and persist it to ``config.output_path``."""

    dash = build_dashboard(config)
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    config.output_path.write_text(dash.model_dump_json(indent=2), encoding="utf-8")
    return dash
