"""Unit tests for the nightly ops run log (spec §11).

Every nightly run appends one structured :class:`RunRecord` — start/end, per-list item counts,
abstains, the judgment provider, how many calls were logged for tomorrow's scoring, and a cost
note — to an append-only JSONL log beside ``dashboard.json``. The log is operations history (not
the artifact), so wall-clock timestamps are allowed here; the artifact itself stays clock-free.
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
from factor_scope.schedule import RunRecord, append_run_log, cost_note, summarize_run

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


def test_cost_note_flags_the_agent_sdk_metering_for_claude_code() -> None:
    # The judgment path can run on headless `claude -p`; from 2026-06-15 it meters against a
    # separate Agent-SDK credit, which the ops log must surface for sizing.
    assert "Agent-SDK" in cost_note("claude_code")
    # The default fake provider is offline and free.
    assert "no" in cost_note("fake").lower() and "cost" in cost_note("fake").lower()


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
    # Round-trips back into a RunRecord.
    assert RunRecord(**first).n_abstain == 1
