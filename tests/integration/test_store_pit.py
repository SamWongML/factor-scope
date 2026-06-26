"""Integration tests for the point-in-time store: append-only + as-of reads."""

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


def test_read_as_of_excluding_skips_flagged_rows_before_the_per_key_collapse() -> None:
    # The watermark wants the latest *settled* row per key, but a provisional spot estimate can sit
    # on top of settled history. Post-filtering read_as_of can't recover it (the key already
    # collapsed to its one latest-overall row); excluding the flag *inside* the windowed read does.
    with DuckDBStore() as store:
        store.append(
            [
                _reading("X", "2026-01-01", 1.0),
                _reading("X", "2026-03-01", 2.0),  # the latest settled bar
                Reading(series="prices", key="X", as_of="2026-03-05", fetched_at="t",
                        payload={"nav": 9.0, "provisional": True}),  # a provisional bar on top
                Reading(series="prices", key="Y", as_of="2026-03-05", fetched_at="t",
                        payload={"nav": 5.0, "provisional": True}),  # Y: a provisional bar only
            ]
        )
        # Plain: the latest-overall row wins, provisional included — the current-estimate read.
        plain = store.read_as_of("prices", "2026-03-31")
        assert {r.key: r.payload["nav"] for r in plain} == {"X": 9.0, "Y": 5.0}
        # excluding: provisional rows are skipped *before* the per-key collapse, so X surfaces its
        # latest settled bar and Y — provisional-only — drops out (it has no settled history).
        settled = store.read_as_of("prices", "2026-03-31", excluding="provisional")
        assert {r.key: r.payload["nav"] for r in settled} == {"X": 2.0}


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


def test_append_dedups_a_refetched_reading_across_nights() -> None:
    # The nightly ingest re-pulls full histories, so the same (series, key, as_of) payload arrives
    # again under a fresh fetched_at (a later night). Identity keys on content, not the fetch stamp,
    # so the re-fetch is a no-op — cross-night, not just within a run's retries.
    with DuckDBStore() as store:
        first = _reading("X", "2026-01-01", 1.0, fetched_at="2026-01-01T22:00:00Z")
        assert store.append([first]) == 1
        again = _reading("X", "2026-01-01", 1.0, fetched_at="2026-02-01T22:00:00Z")  # later night
        assert store.append([again]) == 0  # unchanged payload → no-op despite the fresh fetched_at
        assert store.count("prices") == 1
        assert [r.payload["nav"] for r in store.history("prices", "X")] == [1.0]


def test_a_restatement_at_the_same_as_of_is_a_new_revision() -> None:
    # A genuine restatement — a changed payload at the same as_of, disclosed a later night — is
    # still recorded as a new revision, and read_as_of returns the latest one.
    with DuckDBStore() as store:
        store.append([_reading("X", "2026-01-01", 1.0, fetched_at="2026-01-01T22:00:00Z")])
        added = store.append([_reading("X", "2026-01-01", 2.0, fetched_at="2026-02-01T22:00:00Z")])
        assert added == 1  # exactly one new revision
        assert store.count("prices") == 2  # both revisions retained — the audit trail
        assert [r.payload["nav"] for r in store.history("prices", "X")] == [1.0, 2.0]
        assert store.read_as_of("prices", "2026-06-01")[0].payload["nav"] == 2.0  # latest revision


def test_snapshot_id_is_content_addressed_and_point_in_time() -> None:
    # The id fingerprints the store state knowable as of D: same facts → same id, regardless of
    # insertion order, and a later disclosure (as_of > D) never changes the as-of-D snapshot.
    with DuckDBStore() as a, DuckDBStore() as b:
        rows = [_reading("X", "2026-01-01", 1.0), _reading("Y", "2026-02-01", 2.0)]
        a.append(rows)
        b.append(list(reversed(rows)))
        assert a.snapshot_id("2026-06-01") == b.snapshot_id("2026-06-01")

        a.append([_reading("X", "2026-09-01", 9.0)])  # a future disclosure
        assert a.snapshot_id("2026-06-01") == b.snapshot_id("2026-06-01")  # as-of-D id unchanged
        assert a.snapshot_id("2026-09-30") != b.snapshot_id("2026-06-01")  # but the later id moves


def test_snapshot_id_changes_when_a_knowable_reading_changes() -> None:
    with DuckDBStore() as a, DuckDBStore() as b:
        a.append([_reading("X", "2026-01-01", 1.0)])
        b.append([_reading("X", "2026-01-01", 2.0)])  # same key/date, different value
        assert a.snapshot_id("2026-06-01") != b.snapshot_id("2026-06-01")


def test_snapshot_id_can_exclude_a_series() -> None:
    # The run's own call log is derived output, not ingested research — excluding it keeps the
    # data-snapshot fingerprint stable across re-runs that re-log the same calls.
    with DuckDBStore() as store:
        store.append([_reading("X", "2026-01-01", 1.0)])
        base = store.snapshot_id("2026-06-01", exclude=("calls",))
        store.append(
            [
                Reading(
                    series="calls", key="X", as_of="2026-01-01", fetched_at="t",
                    payload={"action": "hold"},
                )
            ]
        )
        assert store.snapshot_id("2026-06-01", exclude=("calls",)) == base  # excluded → no move
        assert store.snapshot_id("2026-06-01") != base  # but it is a real, knowable fact otherwise


def test_an_oscillating_payload_is_recorded_as_a_new_revision() -> None:
    # Dedup is against the LATEST revision, not the whole history: a value that changes and later
    # returns to an earlier payload is a genuine new disclosure, so it is recorded again.
    with DuckDBStore() as store:
        store.append([_reading("X", "2026-01-01", 1.0, fetched_at="2026-01-01T22:00:00Z")])
        store.append([_reading("X", "2026-01-01", 2.0, fetched_at="2026-02-01T22:00:00Z")])
        added = store.append([_reading("X", "2026-01-01", 1.0, fetched_at="2026-03-01T22:00:00Z")])
        assert added == 1  # back to 1.0, but it differs from the latest (2.0) → a new revision
        assert [r.payload["nav"] for r in store.history("prices", "X")] == [1.0, 2.0, 1.0]
        assert store.read_as_of("prices", "2026-06-01")[0].payload["nav"] == 1.0
