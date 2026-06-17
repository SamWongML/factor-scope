"""End-to-end: a run materializes the per-fund time-series gold, and nightly publishes a replica.

At the entrypoint, ``run`` files each item's night into ``series/<code>.json`` next to the
artifact, and the CLI ``nightly`` job publishes a read-only store replica for isolated ad-hoc
queries after the run.
"""

import pytest
from typer.testing import CliRunner

from factor_scope import series
from factor_scope.cli import app
from factor_scope.config import Config
from factor_scope.pipeline import run
from factor_scope.store import DuckDBStore
from factor_scope.store.replica import ReadReplica

pytestmark = pytest.mark.system

runner = CliRunner()


def test_run_materializes_a_trail_per_item(tmp_path) -> None:
    cfg = Config(output_path=tmp_path / "dashboard.json")
    dash = run(cfg)

    series_dir = series.resolve_series_dir(cfg)
    codes = series.list_codes(series_dir)
    # One trail per item on the artifact (holdings + watchlist + emerging).
    assert len(codes) == len(dash.items)

    trail = series.load(series_dir, codes[0])
    assert trail is not None
    assert [p.as_of for p in trail.points] == [dash.as_of]  # tonight's single point

    # Re-running the night leaves the trail at one point — first write wins, like the history.
    run(Config(output_path=tmp_path / "dashboard.json"))
    again = series.load(series_dir, codes[0])
    assert again is not None and [p.as_of for p in again.points] == [dash.as_of]


def test_nightly_publishes_a_queryable_replica(tmp_path) -> None:
    store_path = tmp_path / "store.duckdb"
    result = runner.invoke(
        app,
        [
            "nightly",
            "--output", str(tmp_path / "dashboard.json"),
            "--store-path", str(store_path),
            "--graph-path", str(tmp_path / "graph.ladybug"),
            "--log-path", str(tmp_path / "nightly.jsonl"),
        ],
    )
    assert result.exit_code == 0, result.output

    replica = store_path.with_suffix(".replica.duckdb")
    assert replica.is_file()

    # The replica is read-only-queryable while the writer's store still exists, never colliding.
    writer = DuckDBStore(store_path)
    try:
        pool = ReadReplica(replica)
        try:
            [(positions,)] = pool.query("SELECT count(*) FROM readings WHERE series = 'positions'")
            assert positions > 0
        finally:
            pool.close()
    finally:
        writer.close()
