"""Discovery, netted — corpus + the two seams → ``themes`` Readings the nightly already consumes.

``discover_themes`` is the pure core: run the topic model, drop the noise, populate each surviving
topic's judgment fields, and shape a ``themes`` payload identical to the hand-authored one *plus* an
``evidence`` list (the cited materials). It writes the **research side** of the snapshot boundary —
dated Readings — leaving the nightly's deterministic reasoning untouched: ``_theme_from_reading``
already ignores the extra ``evidence`` key, so the contract stays compatible.
"""

from __future__ import annotations

from factor_scope.contract import Evidence
from factor_scope.cost import BudgetGuard
from factor_scope.discovery.assess import FieldVerdict, ThemeAssessor
from factor_scope.discovery.topics import StreamDoc, TopicModel
from factor_scope.store import Reading

__all__ = ["build_stream_docs", "discover_themes"]


def build_stream_docs(readings: list[Reading]) -> list[StreamDoc]:
    """Turn ``textstream`` Readings into the corpus the topic model reads (point-in-time)."""

    return [
        StreamDoc(
            doc_id=r.key,
            as_of=r.as_of,
            source=str(r.payload["source"]),
            text=str(r.payload["text"]),
        )
        for r in readings
    ]


def _evidence_payload(label: str, verdict: FieldVerdict) -> dict[str, str]:
    """One cited line, tagged with the field it supports, in the contract's ``Evidence`` shape."""

    e: Evidence = verdict.evidence
    return {"src": e.src, "as_of": e.as_of, "one_line": f"{label}: {e.one_line}"}


def discover_themes(
    docs: list[StreamDoc],
    topic_model: TopicModel,
    assessor: ThemeAssessor,
    *,
    as_of: str,
    fetched_at: str,
    budget: BudgetGuard | None = None,
) -> list[Reading]:
    """Discover candidate themes from the corpus → dated ``themes`` Readings (noise dropped).

    Each surviving (weak/strong) topic becomes one Reading keyed by its label, carrying the
    quantitative trajectory, the constituents seed, the four assessed booleans, and the cited
    ``evidence`` behind them. ``wrapper_exists`` is left optimistic — whether an investable wrapper
    truly exists is settled downstream by the holdings-overlap mapping (an unmapped theme yields an
    empty Stage-B shortlist and simply drops), so discovery stays decoupled from the fund universe.

    With a ``budget`` (the monthly USD ceiling), discovery stops assessing once the month's spend is
    crossed: themes already assessed are written (the store is append-only, so the run is partial
    but valid) and the realised cost of each assessment is charged to the guard. The deterministic
    fake
    meters nothing, so the offline run is never throttled.
    """

    by_id = {d.doc_id: d for d in docs}
    readings: list[Reading] = []
    for topic in topic_model.discover(docs, as_of=as_of):
        if topic.signal == "noise":
            continue  # too faint to act on — never written
        if budget is not None and not budget.affordable():
            break  # the month's budget is spent — stop here; themes so far are valid
        evidence_docs = [by_id[i] for i in topic.doc_ids if i in by_id]
        spent_before = len(assessor.usage)
        assessment = assessor.assess(topic, evidence_docs)
        if budget is not None:
            budget.charge(sum(u.cost_usd for u in assessor.usage[spent_before:]))
        fields = (
            ("broad-adoption", assessment.broad_adoption),
            ("path-to-profit", assessment.path_to_profit),
            ("fad-resistant", assessment.fad_resistant),
            ("lead-chain", assessment.lead_chain),
        )
        payload: dict[str, object] = {
            "acceleration": topic.acceleration,
            "base_level": topic.base_level,
            "breadth": topic.breadth,
            "crowding": topic.crowding,
            "constituents": list(topic.constituents),
            "broad_adoption": assessment.broad_adoption.holds,
            "path_to_profit": assessment.path_to_profit.holds,
            "fad_resistant": assessment.fad_resistant.holds,
            "lead_chain": assessment.lead_chain.holds,
            "wrapper_exists": True,
            "signal": topic.signal,
            "evidence": [_evidence_payload(label, verdict) for label, verdict in fields],
        }
        readings.append(
            Reading(
                series="themes",
                key=topic.label,
                as_of=as_of,
                fetched_at=fetched_at,
                payload=payload,
            )
        )
    return readings
