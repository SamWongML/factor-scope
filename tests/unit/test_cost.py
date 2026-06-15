"""Unit tests for the cost meter — the constant per-call record, its rollup, and the budget guard.

Every model call books one :class:`Usage` tagged with its source of creation (``provider`` /
``model``), priced from a per-model table for token-only providers. Records roll up per
``(provider, model)`` for the run log and append to a cross-job spend ledger the monthly
:class:`BudgetGuard` reads. The deterministic offline path meters nothing, so the ledger stays empty
and the fixtures run reproduces byte-for-byte — exercised here on the pure functions.
"""

import pytest

from factor_scope.cost import (
    BudgetGuard,
    Price,
    ProviderCost,
    Usage,
    append_spend,
    month_to_date_usd,
    price_usd,
    roll_up,
    split_model,
    total_usd,
)

pytestmark = pytest.mark.unit

_PRICES = {
    "deepseek-v4-flash": Price(input_per_mtok=0.14, output_per_mtok=0.28),
    "deepseek-v4-pro": Price(input_per_mtok=0.44, output_per_mtok=0.87),
}


def test_split_model_separates_the_provider_prefix_from_the_model_id() -> None:
    assert split_model("deepseek:deepseek-v4-flash") == ("deepseek", "deepseek-v4-flash")
    # A bare id (a custom base_url endpoint) has no prefix — provider reads as empty, still stable.
    assert split_model("qwen2.5-72b") == ("", "qwen2.5-72b")


def test_price_usd_charges_input_and_output_from_the_table() -> None:
    # 1M input @ $0.14 + 0.5M output @ $0.28 = 0.14 + 0.14 = 0.28.
    assert price_usd("deepseek-v4-flash", 1_000_000, 500_000, _PRICES) == pytest.approx(0.28)


def test_price_usd_meters_an_unpriced_model_at_zero_without_raising() -> None:
    # Tokens are still recorded by the caller; USD is 0 until a price line is added (no crash).
    assert price_usd("opus", 1000, 1000, _PRICES) == 0.0


def test_roll_up_aggregates_per_provider_and_model_deterministically() -> None:
    usages = [
        Usage("deepseek", "deepseek-v4-flash", 100, 40, 0.05),
        Usage("claude_code", "opus", 200, 80, 0.30),
        Usage("deepseek", "deepseek-v4-flash", 10, 4, 0.01),
    ]
    rows = roll_up(usages)
    # Ordered by (provider, model); the two flash calls fold into one row.
    assert rows == (
        ProviderCost("claude_code", "opus", 1, 200, 80, 0.30),
        ProviderCost("deepseek", "deepseek-v4-flash", 2, 110, 44, pytest.approx(0.06)),
    )
    assert total_usd(usages) == pytest.approx(0.36)


def test_roll_up_of_nothing_is_empty() -> None:
    assert roll_up([]) == ()
    assert total_usd([]) == 0.0


def test_budget_guard_is_affordable_until_the_ceiling_is_crossed() -> None:
    guard = BudgetGuard(limit_usd=1.0, spent_usd=0.6)
    assert guard.affordable()
    guard.charge(0.5)  # now $1.10 spent, over the $1.00 ceiling
    assert not guard.affordable()
    assert guard.spent_usd == pytest.approx(1.1)


def test_budget_guard_seeded_at_the_limit_is_not_affordable() -> None:
    assert not BudgetGuard(limit_usd=2.0, spent_usd=2.0).affordable()


def test_append_spend_is_append_only_and_rolls_up_per_provider(tmp_path) -> None:
    ledger = tmp_path / "spend" / "spend.jsonl"  # parent dirs are created
    append_spend(
        ledger,
        as_of="2026-06-05",
        job="nightly",
        usages=[
            Usage("claude_code", "opus", 200, 80, 0.30),
            Usage("claude_code", "opus", 100, 40, 0.15),
        ],
    )
    append_spend(
        ledger,
        as_of="2026-06-06",
        job="discovery",
        usages=[Usage("deepseek", "deepseek-v4-pro", 500, 100, 0.31)],
    )
    lines = ledger.read_text(encoding="utf-8").splitlines()
    # Night one folded its two opus calls into one row; night two appended, never overwrote.
    assert len(lines) == 2


def test_append_spend_writes_nothing_when_there_was_no_spend(tmp_path) -> None:
    # The offline fake meters nothing → an empty ledger → the fixtures run stays byte-for-byte.
    ledger = tmp_path / "spend.jsonl"
    append_spend(ledger, as_of="2026-06-05", job="nightly", usages=[])
    assert not ledger.exists()


def test_month_to_date_sums_only_the_runs_calendar_month(tmp_path) -> None:
    ledger = tmp_path / "spend.jsonl"
    append_spend(ledger, as_of="2026-05-31", job="nightly", usages=[Usage("c", "m", 0, 0, 1.0)])
    append_spend(ledger, as_of="2026-06-04", job="nightly", usages=[Usage("c", "m", 0, 0, 2.0)])
    append_spend(ledger, as_of="2026-06-05", job="nightly", usages=[Usage("c", "m", 0, 0, 4.0)])
    # Only the two June rows count toward a June run's month-to-date.
    assert month_to_date_usd(ledger, "2026-06-05") == pytest.approx(6.0)


def test_month_to_date_of_a_missing_ledger_is_zero(tmp_path) -> None:
    assert month_to_date_usd(tmp_path / "absent.jsonl", "2026-06-05") == 0.0
