"""System tests for the snapshot boundary.

Two halves of the same seam: research/ingest *fetches* and writes dated Readings, while ``run``
reasons over a **frozen snapshot** — deterministically and with no network. These tests pin that
``run`` never fetches (it refuses a live source and makes no socket calls) and that a fixed
snapshot reproduces byte-for-byte, with the artifact self-identifying its snapshot id.
"""

from __future__ import annotations

import socket

import pytest

from factor_scope.config import Config
from factor_scope.pipeline import SnapshotError, build_dashboard, ingest
from factor_scope.store import DuckDBStore

pytestmark = pytest.mark.system


def test_run_over_an_empty_store_refuses_to_fetch_from_live(monkeypatch) -> None:
    # The boundary: `run` reads a snapshot, it does not build one. A live source against an empty
    # store must raise — never construct a live market and fetch (that is `ingest`'s job).
    def _boom(name: str) -> object:
        raise AssertionError("`run` must not construct a live market — fetching is `ingest`'s job")

    monkeypatch.setattr("factor_scope.pipeline.get_market", _boom)
    with pytest.raises(SnapshotError, match="ingest"):
        build_dashboard(Config(source="live"))


def test_a_fixed_snapshot_reproduces_and_carries_its_snapshot_id(tmp_path) -> None:
    # Acceptance: two runs over one frozen snapshot (+ the fake provider) produce identical
    # dashboard.json, and the artifact self-identifies the store state it reasoned over.
    cfg = Config(store_path=tmp_path / "store.duckdb", graph_path=tmp_path / "graph.ladybug")
    ingest(cfg)  # research/ingest writes the dated Readings — the snapshot `run` will read

    first = build_dashboard(cfg)
    second = build_dashboard(cfg)
    assert first.model_dump_json(indent=2) == second.model_dump_json(indent=2)

    with DuckDBStore(cfg.store_path) as store:
        # derived output is excluded — the run's own logged calls and any cached debates, not the
        # read snapshot — exactly as the production fingerprint does, so neither can perturb the id
        expected = store.snapshot_id(first.as_of, exclude=("calls", "debate_cache"))
    assert first.snapshot_id == expected
    assert first.snapshot_id  # non-empty: the artifact names the frozen snapshot it read


def test_ingest_makes_no_network_calls(monkeypatch, tmp_path) -> None:
    # The offline edge: `ingest` replays the committed cassettes through the same universe loop +
    # reconciliation as live, so with every socket path booby-trapped it still fills the store —
    # proving the offline transport (CassetteFeed) reaches for no network.
    def _deny(*args: object, **kwargs: object) -> object:
        raise AssertionError("offline `ingest` attempted a network call — cassettes replay locally")

    monkeypatch.setattr(socket, "getaddrinfo", _deny)
    monkeypatch.setattr(socket, "create_connection", _deny)
    monkeypatch.setattr(socket.socket, "connect", _deny)
    monkeypatch.setattr(socket.socket, "connect_ex", _deny)

    n = ingest(Config(store_path=tmp_path / "store.duckdb", graph_path=tmp_path / "graph.ladybug"))
    assert n > 0  # the store was filled entirely from the local recordings


def test_run_makes_no_network_calls(monkeypatch) -> None:
    # The no-network guard: with every socket path booby-trapped, a fixtures `run` still produces
    # its artifact — proving it reads the frozen snapshot and never reaches for the network.
    def _deny(*args: object, **kwargs: object) -> object:
        raise AssertionError("`run` attempted a network call — fetching belongs to `ingest`")

    monkeypatch.setattr(socket, "getaddrinfo", _deny)
    monkeypatch.setattr(socket, "create_connection", _deny)
    monkeypatch.setattr(socket.socket, "connect", _deny)
    monkeypatch.setattr(socket.socket, "connect_ex", _deny)

    dash = build_dashboard(Config())
    assert dash.items  # the run completed entirely from the offline snapshot
