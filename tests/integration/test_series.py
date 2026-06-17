"""The pre-materialized time-series gold tier — compact per-fund trails, served flat.

At the end of each run the morning's per-fund readings are appended — one point per night — to a
small ``<code>.json`` trail, so charting a fund reads only that artifact. The headline property:
the served trail's read cost is **flat in the size of the store** behind it.
"""

from __future__ import annotations

import pytest

from factor_scope import series
from factor_scope.contract import Band, DashboardItem, FactorState, GateState, ListName
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.integration


def _item(name: str) -> DashboardItem:
    return DashboardItem(
        item=name,
        list=ListName.HOLDINGS,
        gain=0.1,
        gate=GateState.OPEN,
        states=[
            FactorState(factor="trend", level=Band.HIGH, direction="up"),
            FactorState(factor="stale", level=Band.LOW, direction="down", valid=False),
        ],
    )


def _materialize_nights(
    series_dir, code: str, nights: list[str], bars_per_night: int, n_others: int = 0
) -> None:
    store = DuckDBStore(":memory:")
    try:
        for n, as_of in enumerate(nights):
            # The store accrues `bars_per_night` NAV bars each night — its size grows with n. With
            # `n_others`, it also carries that many *other* funds' bars: store breadth grows, but
            # only `code` is materialized, so its trail must stay independent of the rest.
            rows = [
                Reading(
                    series="prices",
                    key=key,
                    as_of=as_of,
                    fetched_at=f"{as_of}T{j:04d}",
                    payload={"nav": 1.0 + n + j * 0.01},
                )
                for key in [code, *(f"OTHER{k}" for k in range(n_others))]
                for j in range(bars_per_night)
            ]
            store.append(rows)
            entries = series.materialize([(code, _item("GF Nasdaq"))], store, as_of)
            series.record(entries, series_dir)
    finally:
        store.close()


NIGHTS = ["2026-06-03", "2026-06-04", "2026-06-05"]


def test_a_trail_is_one_compact_point_per_night(tmp_path) -> None:
    sdir = tmp_path / "series"
    _materialize_nights(sdir, "513100", NIGHTS, bars_per_night=3)

    trail = series.load(sdir, "513100")
    assert trail is not None
    assert trail.code == "513100" and trail.name == "GF Nasdaq"
    assert [p.as_of for p in trail.points] == NIGHTS  # oldest first, one per night
    point = trail.points[-1]
    assert point.nav is not None and point.gain == 0.1 and point.gate is GateState.OPEN
    # Compact: only the valid factor bands ride along, never the per-night evidence.
    assert [(f.factor, f.level) for f in point.factors] == [("trend", Band.HIGH)]
    assert series.list_codes(sdir) == ["513100"]


def test_trail_read_cost_is_flat_in_store_size(tmp_path) -> None:
    # The acceptance property: as the store grows (more NAV bars per night), the served trail stays
    # the same shape — one point per night — so its read cost is constant in the store's size.
    small = tmp_path / "small"
    large = tmp_path / "large"
    _materialize_nights(small, "513100", NIGHTS, bars_per_night=2)
    _materialize_nights(large, "513100", NIGHTS, bars_per_night=500)

    small_trail = series.load(small, "513100")
    large_trail = series.load(large, "513100")
    assert small_trail is not None and large_trail is not None
    # A 250× larger store yields the same number of points — the trail is flat in store size.
    assert len(small_trail.points) == len(large_trail.points) == len(NIGHTS)
    assert [p.as_of for p in small_trail.points] == [p.as_of for p in large_trail.points]


def test_trail_read_is_independent_of_store_breadth(tmp_path) -> None:
    # The other dimension a naive (re-scan-the-store) implementation would feel: many *other* funds.
    # One fund's served trail must be byte-identical — and so equally cheap to read — whether the
    # store holds that fund alone or 200 funds' histories, because the read touches only its file.
    lean = tmp_path / "lean"
    crowded = tmp_path / "crowded"
    _materialize_nights(lean, "513100", NIGHTS, bars_per_night=2)
    _materialize_nights(crowded, "513100", NIGHTS, bars_per_night=2, n_others=200)

    a = series.load(lean, "513100")
    b = series.load(crowded, "513100")
    assert a is not None and b is not None
    assert a.model_dump_json() == b.model_dump_json()  # identical regardless of store breadth
    assert series.list_codes(lean) == series.list_codes(crowded) == ["513100"]


def test_a_code_on_two_lists_yields_one_trail_core_first(tmp_path) -> None:
    # A held fund the funnel also surfaces as emerging is one fund: its per-fund trail keeps the
    # core (holding) projection, not the emerging one, and is recorded exactly once.
    store = DuckDBStore(":memory:")
    try:
        store.append(
            [Reading(series="prices", key="A", as_of="2026-06-05", fetched_at="t",
                     payload={"nav": 5.0})]
        )
        held = _item("Held")  # gain=0.1, gate OPEN
        emerging = DashboardItem(
            item="Same fund (emerging view)",
            list=ListName.EMERGING,
            states=[FactorState(factor="trend", level=Band.LOW, direction="down")],
        )
        entries = series.materialize([("A", held), ("A", emerging)], store, "2026-06-05")
    finally:
        store.close()

    assert len(entries) == 1
    code, name, point = entries[0]
    assert code == "A" and name == "Held"  # the first (core-list) projection won
    assert point.gain == 0.1 and point.gate is GateState.OPEN


def test_a_night_is_recorded_once_first_write_wins(tmp_path) -> None:
    # Re-running a night must not rewrite its point (mirrors the append-only store).
    sdir = tmp_path / "series"
    store = DuckDBStore(":memory:")
    try:
        store.append([Reading(series="prices", key="A", as_of="2026-06-05",
                              fetched_at="t", payload={"nav": 9.0})])
        series.record(series.materialize([("A", _item("First"))], store, "2026-06-05"), sdir)
        series.record(series.materialize([("A", _item("Rewritten"))], store, "2026-06-05"), sdir)
    finally:
        store.close()

    trail = series.load(sdir, "A")
    assert trail is not None
    assert len(trail.points) == 1
    assert trail.name == "First"  # the first recording of the night stands


def test_a_name_with_no_price_keeps_a_factor_trail(tmp_path) -> None:
    sdir = tmp_path / "series"
    store = DuckDBStore(":memory:")
    try:
        series.record(series.materialize([("WATCH", _item("No NAV"))], store, "2026-06-05"), sdir)
    finally:
        store.close()

    trail = series.load(sdir, "WATCH")
    assert trail is not None
    assert trail.points[0].nav is None
    assert [f.factor for f in trail.points[0].factors] == ["trend"]


def test_an_absent_trail_is_none(tmp_path) -> None:
    assert series.load(tmp_path / "series", "nope") is None
    assert series.list_codes(tmp_path / "absent") == []
