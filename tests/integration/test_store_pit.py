"""Integration tests for the point-in-time store: append-only + as-of reads (spec §09)."""

import pytest

from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.integration


def _reading(key: str, as_of: str, nav: float, fetched_at: str = "t") -> Reading:
    return Reading(
        series="prices", key=key, as_of=as_of, fetched_at=fetched_at, payload={"nav": nav}
    )


def test_read_as_of_returns_latest_row_at_or_before_date() -> None:
    with DuckDBStore() as store:
        store.append(
            [
                _reading("X", "2026-01-01", 1.0),
                _reading("X", "2026-03-01", 2.0),
            ]
        )
        # As of a date between the two disclosures, the earlier one is in force.
        feb = store.read_as_of("prices", "2026-02-01")
        assert [(r.key, r.payload["nav"]) for r in feb] == [("X", 1.0)]
        # As of a later date, the newer disclosure wins.
        mar = store.read_as_of("prices", "2026-03-15")
        assert [(r.key, r.payload["nav"]) for r in mar] == [("X", 2.0)]


def test_a_later_disclosure_does_not_rewrite_history() -> None:
    with DuckDBStore() as store:
        store.append([_reading("X", "2026-01-01", 1.0)])
        store.append([_reading("X", "2026-03-01", 2.0)])  # append, not overwrite
        # Both rows are retained — the append-only audit trail.
        assert [r.payload["nav"] for r in store.history("prices", "X")] == [1.0, 2.0]
        # And an as-of read of the earlier date is unchanged by the later disclosure.
        assert store.read_as_of("prices", "2026-01-15")[0].payload["nav"] == 1.0


def test_read_as_of_picks_one_row_per_key() -> None:
    with DuckDBStore() as store:
        store.append(
            [
                _reading("A", "2026-01-01", 1.0),
                _reading("B", "2026-01-01", 9.0),
                _reading("A", "2026-02-01", 1.5),
            ]
        )
        latest = store.read_as_of("prices", "2026-12-31")
        assert [(r.key, r.payload["nav"]) for r in latest] == [("A", 1.5), ("B", 9.0)]


def test_persists_across_connections(tmp_path) -> None:
    path = tmp_path / "store.duckdb"
    with DuckDBStore(path) as store:
        store.append([_reading("X", "2026-01-01", 1.0)])
    # Reopen: the durable, append-only store survives the connection.
    with DuckDBStore(path) as store:
        assert store.count("prices") == 1
        assert store.read_as_of("prices", "2026-06-01")[0].payload["nav"] == 1.0


def test_count_and_empty_series() -> None:
    with DuckDBStore() as store:
        assert store.count() == 0
        assert store.read_as_of("prices", "2026-01-01") == []


def test_append_is_idempotent_on_re_run() -> None:
    # A retried nightly run re-appends the same readings (same fetched_at, derived from as_of).
    # Append-only must not mean append-duplicates: an identical row is a no-op, not a second copy.
    with DuckDBStore() as store:
        batch = [_reading("X", "2026-01-01", 1.0), _reading("Y", "2026-01-01", 2.0)]
        assert store.append(batch) == 2  # first run writes both
        assert store.append(batch) == 0  # re-run writes nothing — already present
        assert store.count("prices") == 2
        assert [r.payload["nav"] for r in store.history("prices", "X")] == [1.0]  # no duplicate


def test_append_keeps_rows_that_differ_only_by_source() -> None:
    # Two sources reading the same fund/day are DISTINCT facts — provenance is part of the identity,
    # so dedup must not collapse an AkShare row and a Baostock row for the same (key, as_of).
    with DuckDBStore() as store:
        akshare = Reading(series="prices", key="X", as_of="2026-01-01", fetched_at="t",
                          payload={"nav": 1.0, "source": "akshare"})
        baostock = akshare.model_copy(update={"payload": {"nav": 1.0, "source": "baostock"}})
        assert store.append([akshare, baostock]) == 2
        assert store.append([akshare, baostock]) == 0  # still idempotent on re-run
        assert store.count("prices") == 2
