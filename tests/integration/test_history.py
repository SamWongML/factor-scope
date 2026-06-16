"""Integration tests for the dashboard history — one immutable artifact per night."""

from pathlib import Path

import pytest

from factor_scope.config import Config
from factor_scope.contract import Dashboard
from factor_scope.history import load, read_index, record, resolve_history_dir

pytestmark = pytest.mark.integration


def _dash(as_of: str) -> Dashboard:
    return Dashboard(as_of=as_of, generated_at=f"{as_of}T22:00:00Z", snapshot_id=f"snap-{as_of}")


def test_record_writes_the_dated_artifact(tmp_path) -> None:
    history = tmp_path / "dashboards"
    path = record(_dash("2026-06-05"), history)
    assert path == history / "2026-06-05.json"
    assert Dashboard.model_validate_json(path.read_text(encoding="utf-8")).as_of == "2026-06-05"
    assert [e.as_of for e in read_index(history).entries] == ["2026-06-05"]


def test_a_recorded_night_is_immutable(tmp_path) -> None:
    # The first recording of an as_of stands: a later run never rewrites an earlier night,
    # mirroring the append-only store. Re-recording returns the same path, untouched.
    history = tmp_path / "dashboards"
    first = record(_dash("2026-06-05"), history)
    original = first.read_bytes()
    revised = Dashboard(
        as_of="2026-06-05", generated_at="2026-06-05T23:30:00Z", snapshot_id="snap-revised"
    )
    again = record(revised, history)
    assert again == first
    assert again.read_bytes() == original


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


def test_reading_the_index_is_o1_in_history_length(tmp_path, monkeypatch) -> None:
    # The catalog is materialized at record time, so listing opens exactly one file — the
    # catalog itself — never the per-night artifacts, no matter how long the history grows.
    history = tmp_path / "dashboards"
    for day in range(1, 9):
        record(_dash(f"2026-06-0{day}"), history)

    opened: list[str] = []
    real_read_text = Path.read_text

    def counting_read_text(self: Path, *args: object, **kwargs: object) -> str:
        opened.append(self.name)
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    read_index(history)
    assert opened == ["index.json"]


def test_the_index_is_rebuildable_from_disk_when_the_catalog_is_missing(tmp_path) -> None:
    # The catalog is a cache over the immutable night files, never the source of truth: lose it
    # and the index rebuilds purely from the artifacts on disk.
    history = tmp_path / "dashboards"
    record(_dash("2026-06-06"), history)
    record(_dash("2026-06-04"), history)
    record(_dash("2026-06-05"), history)
    (history / "index.json").unlink()
    idx = read_index(history)
    assert [e.as_of for e in idx.entries] == ["2026-06-04", "2026-06-05", "2026-06-06"]
    assert all(e.snapshot_id == f"snap-{e.as_of}" for e in idx.entries)


def test_a_corrupt_catalog_falls_back_to_a_disk_rebuild(tmp_path) -> None:
    history = tmp_path / "dashboards"
    record(_dash("2026-06-05"), history)
    (history / "index.json").write_text("not json", encoding="utf-8")
    assert [e.as_of for e in read_index(history).entries] == ["2026-06-05"]


def test_a_disk_rebuild_skips_a_corrupt_night(tmp_path) -> None:
    history = tmp_path / "dashboards"
    record(_dash("2026-06-05"), history)
    (history / "2026-06-06.json").write_text("not json", encoding="utf-8")
    (history / "index.json").unlink()
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
