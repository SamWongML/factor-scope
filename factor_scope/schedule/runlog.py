"""The nightly ops run log — one structured :class:`RunRecord` per run.

This is *operations* telemetry, not the decision artifact: it answers "did last night's job run,
what did it produce, on which provider, and what did it cost". Records are appended to a JSONL file
(append-only, like everything else in the engine) so the history of runs is auditable. Wall-clock
timestamps are fine here — only ``dashboard.json`` must stay clock-free for byte-for-byte
reproducibility.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from factor_scope.contract import Dashboard, LeanAction, ListName
from factor_scope.cost import ProviderCost, Usage, roll_up, total_usd


@dataclass(frozen=True)
class DigestFailure:
    """One item whose digest seat *raised* and was degraded to abstain — recorded for visibility."""

    code: str
    error: str


@dataclass(frozen=True)
class RunRecord:
    """One night's run summary — what ran, what it produced, and what it cost."""

    as_of: str
    started_at: str
    ended_at: str
    snapshot_id: str  # the frozen store-state fingerprint this run read (mirrors the artifact)
    provider: str
    n_items: int
    n_holdings: int
    n_watchlist: int
    n_emerging: int
    n_abstain: int
    n_calls_logged: int  # leans logged tonight for tomorrow's self-scoring
    output_path: str
    # This run's spend, per ``(provider, model)`` — the source of creation behind every USD. Empty
    # on the fake provider (it meters nothing), so the fixtures artifact + log stay byte-for-byte.
    costs: tuple[ProviderCost, ...] = ()
    cost_usd: float = 0.0  # this run's grand total across every model call
    month_to_date_usd: float = 0.0  # spend so far this calendar month, incl. this run
    monthly_budget_usd: float | None = None  # the configured ceiling (None → unlimited)
    budget_exhausted: bool = False  # the monthly guard throttled this run's lower-priority items
    # Items whose seat call failed or was throttled (degraded to abstain). Empty on a clean night.
    digest_failures: tuple[DigestFailure, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_run(
    dash: Dashboard,
    *,
    started_at: str,
    ended_at: str,
    provider: str,
    n_calls_logged: int,
    output_path: str = "",
    usages: tuple[Usage, ...] = (),
    month_to_date_usd: float = 0.0,
    monthly_budget_usd: float | None = None,
    digest_failures: tuple[DigestFailure, ...] = (),
) -> RunRecord:
    """Roll one finished :class:`Dashboard` into a :class:`RunRecord` (counts + provider + cost).

    ``usages`` are this run's per-call records (across the seats and any re-rank); they roll up per
    ``(provider, model)`` into ``costs`` + a grand total. ``month_to_date_usd`` is the calendar
    month's spend including this run, weighed against ``monthly_budget_usd``; a run whose budget was
    crossed (a ``"monthly budget"`` digest failure) is flagged ``budget_exhausted``.
    """

    n_holdings = len(dash.by_list(ListName.HOLDINGS))
    n_watchlist = len(dash.by_list(ListName.WATCHLIST))
    n_emerging = len(dash.by_list(ListName.EMERGING))
    n_abstain = sum(
        1 for it in dash.items if it.lean is not None and it.lean.action is LeanAction.ABSTAIN
    )
    return RunRecord(
        as_of=dash.as_of,
        started_at=started_at,
        ended_at=ended_at,
        snapshot_id=dash.snapshot_id,
        provider=provider,
        n_items=n_holdings + n_watchlist + n_emerging,
        n_holdings=n_holdings,
        n_watchlist=n_watchlist,
        n_emerging=n_emerging,
        n_abstain=n_abstain,
        n_calls_logged=n_calls_logged,
        output_path=output_path,
        costs=roll_up(usages),
        cost_usd=total_usd(usages),
        month_to_date_usd=month_to_date_usd,
        monthly_budget_usd=monthly_budget_usd,
        budget_exhausted=any("monthly budget" in f.error for f in digest_failures),
        digest_failures=digest_failures,
    )


def append_run_log(path: Path, record: RunRecord) -> None:
    """Append one record as a JSON line. Append-only — a new night never overwrites an old one."""

    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record.to_dict(), ensure_ascii=False)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
