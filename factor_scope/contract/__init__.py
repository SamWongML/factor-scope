"""The dashboard.json contract — the single artifact every layer reads and writes.

Mirrors the dashboard's data model and the self-scoring mirror it carries. Every field the
engine can eventually produce already has a home here, so each stage can be built and swapped
against one stable contract. Defaults are chosen so an *under-construction* item (no
states/leans/connections yet) is still valid — that is what keeps the entrypoint runnable at
any point in the engine's build-out.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ListName(StrEnum):
    """The three decision lists the morning artifact is organised into."""

    HOLDINGS = "holdings"
    WATCHLIST = "watchlist"
    EMERGING = "emerging"


class Band(StrEnum):
    """Logic-based bands a factor reading is ranked into against its *own* history.

    Cut-points are constants chosen for economic meaning, never tuned to returns.
    """

    EXTREME_LOW = "extreme_low"
    LOW = "low"
    NEUTRAL = "neutral"
    HIGH = "high"
    EXTREME_HIGH = "extreme_high"


class GateState(StrEnum):
    """The 200-day trend gate — a hard rule."""

    OPEN = "open"  # above the 200-day MA: judgment may lean as the states justify
    CAPPED = "capped"  # below the 200-day MA: lean capped at Hold/Avoid, no exceptions
    UNKNOWN = "unknown"  # not yet computed for this item


class LeanAction(StrEnum):
    """The machine-readable lean. The engine emits a lean a human sizes — never an order."""

    BUY_EARLY = "buy_early"
    HOLD = "hold"
    TRIM = "trim"
    EXIT = "exit"
    AVOID = "avoid"
    ABSTAIN = "abstain"  # too blind to call — abstain rather than guess


class FactorState(BaseModel):
    """A single descriptive factor *state* — never a fitted score."""

    model_config = ConfigDict(frozen=True)

    factor: str
    level: Band
    direction: str  # encodes which way "high" points, e.g. "reversal-down risk"
    evidence: str | None = None  # dated pointer to the reading behind this state
    valid: bool = True  # a failed/stale factor → valid=False and is ignored


class Connection(BaseModel):
    """A holdings overlap surfaced by the deterministic look-through."""

    model_config = ConfigDict(frozen=True)

    shared: str  # the shared (often falling) name, e.g. "中际旭创 ↓"
    also_in: list[str] = Field(default_factory=list)  # other funds on my lists holding it
    lookthrough_wt: float = 0.0  # my total look-through weight to it


class Lean(BaseModel):
    """The calibrated lean a human will size, with its confidence."""

    action: LeanAction
    confidence: float = Field(ge=0.0, le=1.0)
    text: str  # human phrasing, e.g. "Trim / low-conviction"


class ReliabilityBucket(BaseModel):
    """One row of the reliability-by-confidence table."""

    model_config = ConfigDict(frozen=True)

    bucket: float = Field(ge=0.0, le=1.0)  # stated confidence bucket, e.g. 0.7
    realised: float = Field(ge=0.0, le=1.0)  # actual hit-rate in that bucket
    note: str | None = None  # e.g. "overconfident"


class Scorecard(BaseModel):
    """The rolling, descriptive self-scoring mirror.

    It may widen or narrow stated confidence; it can never change a state, open the gate, or
    supply a number. Display is gated on a minimum sample so noise cannot mislead.
    """

    window: str = "60d"
    n: int = 0
    brier: float | None = None
    skill_vs_baserate: str | None = None  # e.g. "+0.04"
    reliability: list[ReliabilityBucket] = Field(default_factory=list)
    weak_patterns: list[str] = Field(default_factory=list)  # e.g. "reversal:xhigh overconfident"


class Evidence(BaseModel):
    """A dated, sourced evidence slot — fetch, don't recall."""

    model_config = ConfigDict(frozen=True)

    src: str
    as_of: str
    one_line: str


class DashboardItem(BaseModel):
    """One object per item on a list.

    The JSON key is ``list``; the Python attribute is ``list_name`` to avoid
    shadowing the builtin. Both names are accepted on input.
    """

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    item: str
    list_name: ListName = Field(alias="list")
    gain: float | None = None  # per-item return vs cost basis (cost vs current NAV)
    states: list[FactorState] = Field(default_factory=list)
    lean: Lean | None = None
    evolution: str | None = None  # e.g. "Hold→Trim (2 nights)"
    flip_trigger: str | None = None
    invalidation: str | None = None
    connections: list[Connection] = Field(default_factory=list)
    connections_flag: bool = False
    scorecard: Scorecard | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    gate: GateState = GateState.UNKNOWN


class Dashboard(BaseModel):
    """The dated morning artifact: one run → one dashboard.json."""

    schema_version: int = 1
    as_of: str  # the as-of date the engine reasoned on (point-in-time)
    generated_at: str  # when this artifact was produced
    snapshot_id: str  # fingerprint of the frozen store state this run read (see store.snapshot_id)
    items: list[DashboardItem] = Field(default_factory=list)

    def by_list(self, name: ListName) -> list[DashboardItem]:
        return [it for it in self.items if it.list_name is name]


def dashboard_json_schema() -> dict[str, Any]:
    """The JSON schema for the artifact — useful for validation and for other tools."""

    return Dashboard.model_json_schema()


__all__ = [
    "Band",
    "Connection",
    "Dashboard",
    "DashboardItem",
    "Evidence",
    "FactorState",
    "GateState",
    "Lean",
    "LeanAction",
    "ListName",
    "ReliabilityBucket",
    "Scorecard",
    "dashboard_json_schema",
]
