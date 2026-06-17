"""The medallion cold tier: old readings live in Hive-partitioned Parquet, reads union hot + cold.

The silver ``readings`` log keeps a recent *hot window* in the DuckDB file and exports older rows to
``series=…/year=…/`` Parquet via :meth:`DuckDBStore.tier_cold`, queried in place. Every read
(``read_as_of``, ``history``, ``count``, ``snapshot_id``) unions the hot table with the cold
partitions, so tiering is invisible to a point-in-time read — yet the hot file stays bounded as
history accrues.
"""

import pytest

from factor_scope.config import Config
from factor_scope.pipeline import ingest
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.integration


def _reading(key: str, as_of: str, nav: float, fetched_at: str = "t") -> Reading:
    return Reading(
        series="prices", key=key, as_of=as_of, fetched_at=fetched_at, payload={"nav": nav}
    )


def _years_of_readings() -> list[Reading]:
    # One bar per key across three calendar years — enough to split into cold (old) + hot (recent).
    return [
        _reading("A", "2023-02-01", 1.0),
        _reading("A", "2024-02-01", 1.5),
        _reading("B", "2024-03-01", 9.0),
        _reading("A", "2025-02-01", 2.0),
        _reading("C", "2025-04-01", 5.0),
    ]


def test_tier_cold_writes_hive_partitions_and_prunes_hot(tmp_path) -> None:
    cold = tmp_path / "cold"
    store = DuckDBStore(tmp_path / "store.duckdb", cold_dir=cold)
    try:
        store.append(_years_of_readings())
        moved = store.tier_cold("2025-01-01")  # everything before 2025 goes cold
    finally:
        store.close()

    # Cold export is laid out as series=…/year=…/ Hive partitions, one per (series, year) tiered.
    assert {p.name for p in (cold / "series=prices").iterdir()} == {"year=2023", "year=2024"}
    assert moved == 3  # the three pre-2025 rows

    # The hot DuckDB file now holds only the recent window — open it without the cold dir to see it.
    hot_only = DuckDBStore(tmp_path / "store.duckdb")
    try:
        assert hot_only.count("prices") == 2  # 2025 rows only
    finally:
        hot_only.close()


def test_point_in_time_read_spanning_hot_and_cold_matches_all_hot(tmp_path) -> None:
    rows = _years_of_readings()

    # An all-hot store: the reference behaviour the tiered store must reproduce byte-for-byte.
    reference = DuckDBStore(tmp_path / "ref.duckdb")
    # A tiered store: same rows, but the pre-2025 ones live in cold Parquet.
    cold = tmp_path / "cold"
    tiered = DuckDBStore(tmp_path / "tiered.duckdb", cold_dir=cold)
    try:
        reference.append(rows)
        tiered.append(rows)
        tiered.tier_cold("2025-01-01")

        for as_of in ("2023-06-01", "2024-06-01", "2025-06-01", "2026-06-01"):
            ref = reference.read_as_of("prices", as_of)
            got = tiered.read_as_of("prices", as_of)
            assert [(r.key, r.as_of, r.payload) for r in got] == [
                (r.key, r.as_of, r.payload) for r in ref
            ], as_of
            # The whole audit trail unions hot + cold transparently.
            assert tiered.history("prices") == reference.history("prices")
            assert tiered.count("prices") == reference.count("prices")
            # And the snapshot fingerprint is identical — tiering never moves the snapshot id.
            assert tiered.snapshot_id(as_of) == reference.snapshot_id(as_of), as_of
    finally:
        reference.close()
        tiered.close()


def test_tier_cold_is_incremental_across_runs(tmp_path) -> None:
    cold = tmp_path / "cold"
    store = DuckDBStore(tmp_path / "store.duckdb", cold_dir=cold)
    try:
        store.append(_years_of_readings())
        first = store.tier_cold("2024-01-01")  # only 2023 goes cold
        store.append([_reading("D", "2023-12-01", 7.0)])  # a late backfill into an old year
        second = store.tier_cold("2025-01-01")  # now 2024 + the backfilled 2023 row go cold

        assert first == 1  # the single 2023 row
        assert second == 3  # the two 2024 rows + the backfilled 2023 row, appended to year=2023
        # The union still returns the full, deduped history at every point in time.
        latest = store.read_as_of("prices", "2026-06-01")
        assert {(r.key, r.payload["nav"]) for r in latest} == {
            ("A", 2.0),
            ("B", 9.0),
            ("C", 5.0),
            ("D", 7.0),
        }
    finally:
        store.close()


def test_hot_window_stays_bounded_across_a_multi_year_ingest(tmp_path) -> None:
    # Simulate many nightly batches over years, tiering everything older than a one-year hot window
    # after each batch. The hot DuckDB file must stay bounded even as the unioned history grows.
    from datetime import date, timedelta

    cold = tmp_path / "cold"
    path = tmp_path / "store.duckdb"
    store = DuckDBStore(path, cold_dir=cold)
    try:
        day = date(2023, 1, 1)
        for _ in range(36):  # ~3 years of monthly batches
            as_of = day.isoformat()
            store.append([_reading("A", as_of, 1.0), _reading("B", as_of, 2.0)])
            cutoff = (day - timedelta(days=365)).isoformat()
            store.tier_cold(cutoff)
            day += timedelta(days=30)
        total = store.count("prices")
    finally:
        store.close()

    assert total == 72  # the full history is still readable through the union (36 batches × 2 keys)

    hot_only = DuckDBStore(path)
    try:
        # The hot file holds only the rolling year, not the whole accreting history.
        assert hot_only.count("prices") <= 28  # ~13 months × 2 keys, never the full 72
    finally:
        hot_only.close()


def test_ingest_tiers_cold_when_cold_dir_configured(tmp_path) -> None:
    # The nightly ingest tiers everything outside the hot window once a cold dir is configured.
    cold = tmp_path / "cold"
    store_path = tmp_path / "store.duckdb"
    config = Config(
        source="fixtures",
        store_path=store_path,
        graph_path=tmp_path / "graph.ladybug",
        cold_dir=cold,
        hot_window_days=0,  # tier every reading dated before tonight
    )
    ingest(config)

    assert cold.exists() and any(cold.glob("**/*.parquet"))  # cold partitions were written
    union = DuckDBStore(store_path, cold_dir=cold)
    hot_only = DuckDBStore(store_path)
    try:
        # The historical price bars tiered out of the hot file but stay visible through the union.
        assert union.count("prices") > hot_only.count("prices")
    finally:
        union.close()
        hot_only.close()
