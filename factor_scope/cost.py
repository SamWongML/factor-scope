"""Cost telemetry + the monthly budget guard — every model call, one constant record.

factor-scope spends on three model surfaces, each on its own provider and model: the nightly
bull/bear→synthesis seats (``claude_code``), the theme-discovery draft+judge pass, and the emerging
re-rank (both DeepSeek). This module is the one place that meters them.

The unit is :class:`Usage` — a single call's tokens + USD tagged with *who produced it*
(``provider`` / ``model``), the source of creation behind every spend. Its shape is the same
whatever model ran, so switching a model is a config edit, never a telemetry change (the
OpenTelemetry GenAI conventions name these ``gen_ai.system`` / ``gen_ai.request.model`` /
``gen_ai.usage.*``). ``claude_code`` reports its USD directly; token-only providers are priced from
:class:`Price`, the per-model table you maintain. Records :func:`roll_up` per ``(provider, model)``
for the run log and :func:`append_spend` to a cross-job ledger, and :class:`BudgetGuard` reads its
:func:`month_to_date_usd` to throttle fresh spend once the configured monthly ceiling is crossed — a
hard cap outside the model, exactly like the trend gate.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Price:
    """A model's list price — USD per 1M input / output tokens (one line per model you charge)."""

    input_per_mtok: float
    output_per_mtok: float


@dataclass(frozen=True)
class Usage:
    """One model call's cost, tagged with its source of creation.

    ``provider`` (the vendor/gateway — ``claude_code`` / ``deepseek`` …) and ``model`` (the concrete
    id) are the provenance: every spend traces to who produced it, and the record's shape is the
    same for any model, so switching one is a config edit, never a telemetry change.
    """

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class ProviderCost:
    """One run's spend rolled up for a single ``(provider, model)`` — its tokens + cost."""

    provider: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


def split_model(spec_model: str) -> tuple[str, str]:
    """Split a provider-prefixed id into ``(provider, model)``.

    ``"deepseek:deepseek-v4-flash"`` → ``("deepseek", "deepseek-v4-flash")``; a bare id (a custom
    ``base_url`` endpoint) has no prefix → the provider reads as empty, still a stable tag.
    """

    provider, sep, model = spec_model.partition(":")
    return (provider, model) if sep else ("", provider)


def price_usd(
    model: str, input_tokens: int, output_tokens: int, prices: Mapping[str, Price]
) -> float:
    """USD for one call, priced from the table.

    An unpriced model meters 0 USD — its tokens and ``(provider, model)`` are still recorded, so an
    unbudgeted line shows up and you add its :class:`Price` to start charging it (never raises).
    """

    price = prices.get(model)
    if price is None:
        return 0.0
    return input_tokens / 1e6 * price.input_per_mtok + output_tokens / 1e6 * price.output_per_mtok


def roll_up(usages: Iterable[Usage]) -> tuple[ProviderCost, ...]:
    """Aggregate per ``(provider, model)``, ordered by provider then model (deterministic)."""

    agg: dict[tuple[str, str], ProviderCost] = {}
    for u in usages:
        key = (u.provider, u.model)
        prior = agg.get(key)
        if prior is None:
            agg[key] = ProviderCost(
                u.provider, u.model, 1, u.input_tokens, u.output_tokens, u.cost_usd
            )
        else:
            agg[key] = ProviderCost(
                u.provider,
                u.model,
                prior.calls + 1,
                prior.input_tokens + u.input_tokens,
                prior.output_tokens + u.output_tokens,
                prior.cost_usd + u.cost_usd,
            )
    return tuple(agg[key] for key in sorted(agg))


def total_usd(usages: Iterable[Usage]) -> float:
    """This run's grand-total USD across every call."""

    return sum(u.cost_usd for u in usages)


def append_spend(path: Path, *, as_of: str, job: str, usages: Iterable[Usage]) -> None:
    """Append this run's per-``(provider, model)`` rollup to the append-only spend ledger.

    One JSON line per ``(provider, model)``, tagged with the run's ``as_of`` and the ``job`` that
    spent it. Nothing is written when there was no spend (the offline fake meters nothing → the
    ledger stays empty → the fixtures run reproduces byte-for-byte).
    """

    rows = roll_up(usages)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            line = json.dumps({"as_of": as_of, "job": job, **asdict(row)}, ensure_ascii=False)
            fh.write(line + "\n")


def month_to_date_usd(path: Path, as_of: str) -> float:
    """Sum the ledger's cost over every row in the same calendar month as ``as_of``.

    The cross-job budget window — both the nightly seats and the research job feed it. A missing
    ledger is no spend yet (``0.0``).
    """

    if not path.exists():
        return 0.0
    month = as_of[:7]  # YYYY-MM
    total = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if str(row.get("as_of", ""))[:7] == month:
            total += float(row.get("cost_usd", 0.0))
    return total


class BudgetGuard:
    """A month-to-date USD ceiling on fresh spend — the budget cap, outside the model like the gate.

    Seeded with the month's prior spend (from the ledger); :meth:`affordable` holds until the
    running total reaches the limit, and :meth:`charge` books each fresh call's realised cost. Once
    the ceiling is crossed the caller degrades the rest of the run (abstain-with-error / fewer
    themes), so a partial-but-valid artifact still ships.
    """

    def __init__(self, limit_usd: float, spent_usd: float = 0.0) -> None:
        self._limit = limit_usd
        self._spent = spent_usd

    @property
    def spent_usd(self) -> float:
        return self._spent

    def affordable(self) -> bool:
        """``True`` while the month's running total is still under the ceiling."""

        return self._spent < self._limit

    def charge(self, usd: float) -> None:
        """Book a fresh call's realised cost against the month's running total."""

        self._spent += usd


__all__ = [
    "BudgetGuard",
    "Price",
    "ProviderCost",
    "Usage",
    "append_spend",
    "month_to_date_usd",
    "price_usd",
    "roll_up",
    "split_model",
    "total_usd",
]
