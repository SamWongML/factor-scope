"""System test for the one-shot nightly job.

End-to-end over the bundled fixtures: ``factor-scope nightly`` runs the whole pipeline
(ingest → compute → digest → write ``dashboard.json``), appends one ops :class:`RunRecord` to the
run log, and — crucially — persists tonight's leans as calls in the *durable* store so tomorrow's
self-scoring loop has something to score. This stays green at every later boundary; it is the
production entrypoint.
"""

import json

import pytest
from typer.testing import CliRunner

from factor_scope.cli import app
from factor_scope.config import Config
from factor_scope.contract import Dashboard, LeanAction, ListName
from factor_scope.digest import Case, DigestInput, Proposal, Side
from factor_scope.pipeline import build_dashboard, nightly
from factor_scope.scoring import read_calls
from factor_scope.store import DuckDBStore

pytestmark = pytest.mark.system

runner = CliRunner()


def _paths(tmp_path):
    return dict(
        output=tmp_path / "dashboard.json",
        store=tmp_path / "store.duckdb",
        graph=tmp_path / "graph.ladybug",
        log=tmp_path / "nightly.jsonl",
    )


def test_nightly_entrypoint_writes_artifact_and_run_log(tmp_path) -> None:
    p = _paths(tmp_path)
    result = runner.invoke(
        app,
        [
            "nightly",
            "--output", str(p["output"]),
            "--store-path", str(p["store"]),
            "--graph-path", str(p["graph"]),
            "--log-path", str(p["log"]),
        ],
    )
    assert result.exit_code == 0, result.output

    # The artifact is schema-valid and carries the full book (2 holdings + 1 watch + 3 emerging).
    dash = Dashboard.model_validate(json.loads(p["output"].read_text(encoding="utf-8")))
    assert dash.as_of == "2026-06-05"
    assert len(dash.items) == 6

    # One ops record was appended, summarising the run.
    lines = p["log"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["provider"] == "fake"
    assert record["n_items"] == 6
    assert record["n_holdings"] == 2 and record["n_emerging"] == 3
    assert record["output_path"] == str(p["output"])
    # The run log records the frozen snapshot the run read — the same id the artifact carries.
    assert record["snapshot_id"] and record["snapshot_id"] == dash.snapshot_id


def test_nightly_persists_tonights_leans_as_calls_for_next_day_scoring(tmp_path) -> None:
    p = _paths(tmp_path)
    cfg = Config(
        output_path=p["output"],
        store_path=p["store"],
        graph_path=p["graph"],
        log_path=p["log"],
    )
    clock = iter(["2026-06-05T22:00:00Z", "2026-06-05T22:00:11Z"]).__next__
    dash, record = nightly(cfg, clock=clock)

    # Every emitted lean became a falsifiable call stamped tonight — tomorrow's scoring fuel.
    store = DuckDBStore(p["store"])
    try:
        tonight = [c for c in read_calls(store, dash.as_of) if c.as_of == dash.as_of]
    finally:
        store.close()
    assert len(tonight) == 6  # one per item on the three lists
    assert record.n_calls_logged == 6

    # The record's timestamps came from the injected clock (deterministic in the test).
    assert record.started_at == "2026-06-05T22:00:00Z"
    assert record.ended_at == "2026-06-05T22:00:11Z"


def test_nightly_is_rerunnable_without_double_logging(tmp_path) -> None:
    # Re-running the same night must not double-count calls (append-only store, idempotent night).
    p = _paths(tmp_path)
    cfg = Config(
        output_path=p["output"], store_path=p["store"], graph_path=p["graph"], log_path=p["log"]
    )
    first, _ = nightly(cfg)
    second, _ = nightly(cfg)
    assert first.model_dump_json(indent=2) == second.model_dump_json(indent=2)

    store = DuckDBStore(p["store"])
    try:
        tonight = [c for c in read_calls(store, first.as_of) if c.as_of == first.as_of]
    finally:
        store.close()
    assert len(tonight) == 6  # still six, not twelve

    # Two nights of ops history, though (the log is append-only).
    assert len(p["log"].read_text(encoding="utf-8").splitlines()) == 2


class _BoomProvider:
    """A judgment provider whose seats always fail (stands in for a missing/broken `claude`)."""

    name = "boom"

    def argue(self, side: Side, brief: DigestInput) -> Case:
        raise RuntimeError("claude binary missing")

    def seats(self, brief: DigestInput) -> tuple[Case, Case]:
        return self.argue(Side.BULL, brief), self.argue(Side.BEAR, brief)

    def synthesize(
        self, brief: DigestInput, bull: Case, bear: Case, *, present_bear_first: bool = False
    ) -> Proposal:
        raise AssertionError("synthesis is unreachable once a seat has failed")


def test_a_seat_budget_cap_abstains_the_lowest_priority_overflow(tmp_path) -> None:
    # A safety ceiling for theme-rich nights: with a cap below the book size the core book argues
    # by priority (holdings → watchlist → emerging) and the overflow degrades to abstain-with-error
    # in the run log — the ceiling lives outside the model, exactly like the trend gate.
    p = _paths(tmp_path)
    cfg = Config(
        output_path=p["output"],
        store_path=p["store"],
        graph_path=p["graph"],
        log_path=p["log"],
        max_debate_items=2,
    )
    dash, record = nightly(cfg)

    # Two holdings fit the budget; the watchlist + three emerging (four items) overflow it.
    over = [f for f in record.digest_failures if "seat budget" in f.error]
    assert len(over) == 4
    holdings = [it for it in dash.items if it.list_name is ListName.HOLDINGS]
    overflow = [it for it in dash.items if it.list_name is not ListName.HOLDINGS]
    assert len(holdings) == 2 and len(overflow) == 4
    # The budgeted core keeps its real lean; every overflow item shows an ordinary abstain.
    assert all(it.lean is not None and it.lean.action is not LeanAction.ABSTAIN for it in holdings)
    assert all(it.lean is not None and it.lean.action is LeanAction.ABSTAIN for it in overflow)


class _CountingProvider:
    """A real-shaped provider that counts how often the expensive seats are argued."""

    name = "counting"

    def __init__(self) -> None:
        self.seat_calls = 0

    def argue(self, side: Side, brief: DigestInput) -> Case:
        return Case(side=side, strength=2.0 if side is Side.BULL else 1.0, confidence=0.6)

    def seats(self, brief: DigestInput) -> tuple[Case, Case]:
        self.seat_calls += 1
        return self.argue(Side.BULL, brief), self.argue(Side.BEAR, brief)

    def synthesize(
        self, brief: DigestInput, bull: Case, bear: Case, *, present_bear_first: bool = False
    ) -> Proposal:
        return Proposal(action=LeanAction.HOLD, confidence=0.7)


def test_a_second_night_reuses_cached_debates_on_a_durable_store(tmp_path, monkeypatch) -> None:
    # The seats are the nightly cost. Against a durable store, a real provider argues every item the
    # first night and *none* the second — the unchanged briefs hit the persisted debate cache. The
    # fake-only offline path never caches, so this needs a non-fake provider to observe the reuse.
    provider = _CountingProvider()
    monkeypatch.setattr("factor_scope.pipeline.get_provider", lambda name, **_: provider)

    p = _paths(tmp_path)
    cfg = Config(
        output_path=p["output"],
        store_path=p["store"],
        graph_path=p["graph"],
        log_path=p["log"],
        provider="claude_code",
    )
    build_dashboard(cfg)
    after_first = provider.seat_calls
    build_dashboard(cfg)

    assert after_first > 0, "the first night must actually argue the seats"
    assert provider.seat_calls == after_first  # the second night re-argued nothing — all reused


def test_nightly_degrades_a_failing_provider_to_abstain_and_logs_it(tmp_path, monkeypatch) -> None:
    # The P0 invariant: a seat failure must not abort the night. Every item that reaches the broken
    # provider degrades to abstain, the artifact is still written, and the run log records the
    # failures (with the error) so a broken night is visible — the log write used to be skipped.
    monkeypatch.setattr(
        "factor_scope.pipeline.get_provider", lambda name, **_: _BoomProvider()
    )

    p = _paths(tmp_path)
    cfg = Config(
        output_path=p["output"],
        store_path=p["store"],
        graph_path=p["graph"],
        log_path=p["log"],
        provider="claude_code",
    )
    dash, record = nightly(cfg)

    # The run completed on a valid, sparser (all-abstain) artifact.
    Dashboard.model_validate(json.loads(p["output"].read_text(encoding="utf-8")))
    assert dash.items and all(
        it.lean is not None and it.lean.action is LeanAction.ABSTAIN for it in dash.items
    )

    # Each degraded item is recorded in the run log with its code and the seat error.
    assert record.digest_failures, "a failing provider must leave a trace in the run log"
    assert all(f.code and "claude binary missing" in f.error for f in record.digest_failures)

    # The single run-log line was written (the bug: it never executed on failure) and carries them.
    lines = p["log"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    logged = json.loads(lines[0])
    assert len(logged["digest_failures"]) == len(record.digest_failures)
    assert logged["digest_failures"][0]["code"] and logged["digest_failures"][0]["error"]
