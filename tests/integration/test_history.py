"""Integration tests for the dashboard history — one immutable artifact per night + its index."""

import pytest

from factor_scope.config import Config
from factor_scope.contract import Dashboard
from factor_scope.history import INDEX_NAME, load, read_index, record, resolve_history_dir

pytestmark = pytest.mark.integration


def _dash(as_of: str) -> Dashboard:
    return Dashboard(as_of=as_of, generated_at=f"{as_of}T22:00:00Z", snapshot_id=f"snap-{as_of}")


def test_record_writes_the_dated_artifact_and_index(tmp_path) -> None:
    history = tmp_path / "dashboards"
    path = record(_dash("2026-06-05"), history)
    assert path == history / "2026-06-05.json"
    assert Dashboard.model_validate_json(path.read_text(encoding="utf-8")).as_of == "2026-06-05"
    assert [e.as_of for e in read_index(history).entries] == ["2026-06-05"]
    assert (history / INDEX_NAME).is_file()


def test_rerecording_the_same_night_is_byte_identical(tmp_path) -> None:
    # Functional-batch idempotency: the same night over the same snapshot rewrites itself
    # byte-for-byte; nothing in the history path may touch the wall clock.
    history = tmp_path / "dashboards"
    dash = _dash("2026-06-05")
    first = record(dash, history).read_bytes()
    first_index = (history / INDEX_NAME).read_bytes()
    second = record(dash, history).read_bytes()
    assert first == second
    assert (history / INDEX_NAME).read_bytes() == first_index


def test_index_lists_nights_oldest_first_with_their_fingerprints(tmp_path) -> None:
    history = tmp_path / "dashboards"
    record(_dash("2026-06-06"), history)
    record(_dash("2026-06-04"), history)
    record(_dash("2026-06-05"), history)
    idx = read_index(history)
    assert [e.as_of for e in idx.entries] == ["2026-06-04", "2026-06-05", "2026-06-06"]
    assert all(e.snapshot_id == f"snap-{e.as_of}" for e in idx.entries)
    assert all(e.n_items == 0 for e in idx.entries)


def test_an_unreadable_night_degrades_to_absence_in_the_index(tmp_path) -> None:
    # Invalid inputs degrade, never raise: a corrupt file drops out of the index, the rest stay.
    history = tmp_path / "dashboards"
    record(_dash("2026-06-05"), history)
    (history / "2026-06-06.json").write_text("not json", encoding="utf-8")
    assert [e.as_of for e in read_index(history).entries] == ["2026-06-05"]


def test_load_reopens_a_recorded_night_or_returns_none(tmp_path) -> None:
    history = tmp_path / "dashboards"
    dash = _dash("2026-06-05")
    record(dash, history)
    assert load(history, "2026-06-05") == dash
    assert load(history, "2026-06-04") is None


def test_an_empty_or_missing_history_reads_as_an_empty_index(tmp_path) -> None:
    assert read_index(tmp_path / "absent").entries == []


def test_history_dir_defaults_next_to_the_artifact(tmp_path) -> None:
    derived = Config(output_path=tmp_path / "out" / "dashboard.json")
    assert resolve_history_dir(derived) == tmp_path / "out" / "dashboards"
    explicit = Config(
        output_path=tmp_path / "dashboard.json", history_dir=tmp_path / "nights"
    )
    assert resolve_history_dir(explicit) == tmp_path / "nights"
