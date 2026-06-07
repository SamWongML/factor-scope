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

# When claude -p starts metering against a separate Agent-SDK credit — used to size the run.
_AGENT_SDK_DATE = "2026-06-15"


@dataclass(frozen=True)
class RunRecord:
    """One night's run summary — what ran, what it produced, and what it cost."""

    as_of: str
    started_at: str
    ended_at: str
    provider: str
    n_items: int
    n_holdings: int
    n_watchlist: int
    n_emerging: int
    n_abstain: int
    n_calls_logged: int  # leans logged tonight for tomorrow's self-scoring
    output_path: str
    cost_note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def cost_note(provider: str) -> str:
    """A one-line budgeting note for the run's judgment provider."""

    if provider == "claude_code":
        return (
            "provider=claude_code — headless `claude -p`; "
            f"from {_AGENT_SDK_DATE} it meters against a separate Agent-SDK credit, "
            "so size the nightly run (≈one bull+bear+synthesis per item) against that budget"
        )
    if provider == "fake":
        return "provider=fake — offline deterministic stub, no API cost"
    return f"provider={provider} — verify its cost model before scheduling"


def summarize_run(
    dash: Dashboard,
    *,
    started_at: str,
    ended_at: str,
    provider: str,
    n_calls_logged: int,
    output_path: str = "",
) -> RunRecord:
    """Roll one finished :class:`Dashboard` into a :class:`RunRecord` (counts + provider + cost)."""

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
        provider=provider,
        n_items=n_holdings + n_watchlist + n_emerging,
        n_holdings=n_holdings,
        n_watchlist=n_watchlist,
        n_emerging=n_emerging,
        n_abstain=n_abstain,
        n_calls_logged=n_calls_logged,
        output_path=output_path,
        cost_note=cost_note(provider),
    )


def append_run_log(path: Path, record: RunRecord) -> None:
    """Append one record as a JSON line. Append-only — a new night never overwrites an old one."""

    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record.to_dict(), ensure_ascii=False)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
