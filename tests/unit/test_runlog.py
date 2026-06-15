"""Unit tests for the nightly ops run log.

Every nightly run appends one structured :class:`RunRecord` — start/end, per-list item counts,
abstains, the judgment provider, how many calls were logged for tomorrow's scoring, and per-provider
cost + budget telemetry — to an append-only JSONL log beside ``dashboard.json``. The log is
operations history (not the artifact), so wall-clock timestamps are allowed here; the artifact
itself stays clock-free.
"""

import json

import pytest

from factor_scope.contract import (
    Dashboard,
    DashboardItem,
    Lean,
    LeanAction,
    ListName,
)
from factor_scope.cost import ProviderCost, Usage
from factor_scope.schedule import DigestFailure, RunRecord, append_run_log, summarize_run

pytestmark = pytest.mark.unit


def _dash() -> Dashboard:
    def item(name: str, lst: ListName, action: LeanAction) -> DashboardItem:
        return DashboardItem(
            item=name,
            list=lst,
            lean=Lean(action=action, confidence=0.5, text=action.value),
        )

    return Dashboard(
        as_of="2026-06-05",
        generated_at="2026-06-05T22:00:00Z",
        snapshot_id="snap-test",
        items=[
            item("光通信", ListName.HOLDINGS, LeanAction.TRIM),
            item("通信ETF", ListName.HOLDINGS, LeanAction.HOLD),
            item("科创芯片ETF", ListName.WATCHLIST, LeanAction.ABSTAIN),
            item("储能ETF", ListName.EMERGING, LeanAction.AVOID),
        ],
    )


def test_summarize_counts_items_per_list_and_abstains() -> None:
    record = summarize_run(
        _dash(),
        started_at="2026-06-05T22:00:01Z",
        ended_at="2026-06-05T22:00:09Z",
        provider="fake",
        n_calls_logged=4,
    )
    assert record.as_of == "2026-06-05"
    assert (record.n_holdings, record.n_watchlist, record.n_emerging) == (2, 1, 1)
    assert record.n_items == 4
    assert record.n_abstain == 1  # only the watchlist item abstained
    assert record.n_calls_logged == 4
    assert record.provider == "fake"
    assert record.snapshot_id == "snap-test"  # the run log records which snapshot was read


def test_summarize_rolls_usage_into_per_provider_cost_and_a_total() -> None:
    record = summarize_run(
        _dash(),
        started_at="2026-06-05T22:00:01Z",
        ended_at="2026-06-05T22:00:09Z",
        provider="claude_code",
        n_calls_logged=4,
        usages=(
            Usage("claude_code", "opus", 200, 80, 0.30),
            Usage("claude_code", "opus", 100, 40, 0.15),
            Usage("deepseek", "deepseek-v4-flash", 50, 10, 0.01),
        ),
        month_to_date_usd=5.46,
        monthly_budget_usd=20.0,
    )
    # The two opus seat calls fold into one row; the re-rank's deepseek call is its own row — every
    # spend traces to its (provider, model) source of creation.
    assert record.costs == (
        ProviderCost("claude_code", "opus", 2, 300, 120, pytest.approx(0.45)),
        ProviderCost("deepseek", "deepseek-v4-flash", 1, 50, 10, pytest.approx(0.01)),
    )
    assert record.cost_usd == pytest.approx(0.46)
    assert record.month_to_date_usd == 5.46 and record.monthly_budget_usd == 20.0
    assert record.budget_exhausted is False


def test_summarize_flags_a_budget_throttled_run() -> None:
    record = summarize_run(
        _dash(),
        started_at="2026-06-05T22:00:01Z",
        ended_at="2026-06-05T22:00:09Z",
        provider="claude_code",
        n_calls_logged=2,
        digest_failures=(DigestFailure(code="512580", error="monthly budget exhausted"),),
    )
    assert record.budget_exhausted is True


def test_a_fake_run_carries_no_cost_so_the_log_stays_deterministic() -> None:
    record = summarize_run(
        _dash(),
        started_at="2026-06-05T22:00:01Z",
        ended_at="2026-06-05T22:00:09Z",
        provider="fake",
        n_calls_logged=4,
    )
    assert record.costs == () and record.cost_usd == 0.0
    assert record.monthly_budget_usd is None and record.budget_exhausted is False


def test_append_run_log_is_append_only_jsonl(tmp_path) -> None:
    path = tmp_path / "logs" / "nightly.jsonl"  # parent dirs are created
    record = summarize_run(
        _dash(),
        started_at="2026-06-05T22:00:01Z",
        ended_at="2026-06-05T22:00:09Z",
        provider="fake",
        n_calls_logged=4,
    )
    append_run_log(path, record)
    append_run_log(path, record)  # a second night appends, never overwrites

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["as_of"] == "2026-06-05"
    assert first["n_items"] == 4
    assert first["provider"] == "fake"
    assert first["snapshot_id"] == "snap-test"
    # Round-trips back into a RunRecord.
    assert RunRecord(**first).n_abstain == 1
