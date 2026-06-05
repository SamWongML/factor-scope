"""Render a :class:`~factor_scope.contract.Dashboard` to a readable terminal summary (L6).

This is the human review surface: ``factor-scope run`` prints it so the current state of the
engine is reviewable at every phase. It degrades gracefully — fields not yet produced by the
current phase simply read as "—".
"""

from __future__ import annotations

from factor_scope.contract import Band, Dashboard, DashboardItem, ListName, Scorecard

_LIST_TITLES = {
    ListName.HOLDINGS: "HOLDINGS  (hold · trim · exit?)",
    ListName.WATCHLIST: "WATCHLIST  (buy-early trigger?)",
    ListName.EMERGING: "EMERGING  (promote a weak signal?)",
}


def _states_line(item: DashboardItem) -> str:
    """A compact read of the factor battery: valid count + the active (non-neutral) reads."""

    if not item.states:
        return "        states: —"
    valid = [s for s in item.states if s.valid]
    active = [s for s in valid if s.level is not Band.NEUTRAL]
    head = f"        states: {len(valid)}/{len(item.states)} valid"
    if not active:
        return head + " (all neutral)"
    reads = "; ".join(f"{s.factor} {s.level.value}→{s.direction}" for s in active)
    return f"{head}\n          {reads}"


def _connections_line(item: DashboardItem) -> str | None:
    """The look-through overlaps: shared names, who else of mine holds them, and my weight."""

    if not item.connections:
        return None
    reads = "; ".join(
        f"{c.shared} also in {', '.join(c.also_in)} (look-through {c.lookthrough_wt:.1%})"
        for c in item.connections
    )
    return f"        connections: {reads}"


def _item_line(item: DashboardItem) -> str:
    lean = item.lean.text if item.lean else "—"
    conf = f"{item.lean.confidence:.2f}" if item.lean else "—"
    gain = f"{item.gain:+.1%}" if item.gain is not None else "—"
    gate = item.gate.value
    flag = " ⚠connections" if item.connections_flag else ""
    evidence = item.evidence[0].one_line if item.evidence else "—"
    lines = [
        f"    • {item.item}  (gain {gain})",
        f"        lean: {lean}  (conf {conf})  gate: {gate}{flag}",
    ]
    if item.evolution:
        lines.append(f"        evolution: {item.evolution}")
    if item.flip_trigger:
        lines.append(f"        flip if: {item.flip_trigger}")
    if item.invalidation:
        lines.append(f"        wrong if: {item.invalidation}")
    lines.append(_states_line(item))
    connections = _connections_line(item)
    if connections is not None:
        lines.append(connections)
    lines.append(f"        evidence: {evidence}")
    for extra in item.evidence[1:]:  # further dated lines (e.g. the emerging Stage-B comparison)
        lines.append(f"                  {extra.one_line}")
    return "\n".join(lines)


def _scorecard_lines(card: Scorecard) -> list[str]:
    """The self-scoring mirror (spec §06): how the last leans resolved. Descriptive only."""

    head = f"  SELF-SCORING MIRROR  —  n={card.n} resolved calls ({card.window})"
    if card.brier is None:  # gated: too thin a record to read
        return [head + " — gated (sample too small)", ""]
    skill = card.skill_vs_baserate or "n/a"
    lines = [head, f"    Brier {card.brier:.3f}   skill vs base-rate {skill}"]
    for b in card.reliability:
        note = f"  ({b.note})" if b.note else ""
        lines.append(f"    conf {b.bucket:.1f} → realised {b.realised:.0%}{note}")
    for w in card.weak_patterns:
        lines.append(f"    ⚠ {w}")
    lines.append("")
    return lines


def render(dash: Dashboard) -> str:
    """Return the morning artifact as plain text."""

    lines: list[str] = []
    lines.append("◆ WEALTH-ASSISTANT ENGINE — morning artifact")
    lines.append(f"  as_of: {dash.as_of}   generated_at: {dash.generated_at}")
    lines.append(f"  items: {len(dash.items)}   (schema v{dash.schema_version})")
    lines.append("")
    card = next((it.scorecard for it in dash.items if it.scorecard is not None), None)
    if card is not None:
        lines.extend(_scorecard_lines(card))

    for name in (ListName.HOLDINGS, ListName.WATCHLIST, ListName.EMERGING):
        group = dash.by_list(name)
        lines.append(f"  {_LIST_TITLES[name]}  —  {len(group)} item(s)")
        if not group:
            lines.append("    (none)")
        for item in group:
            lines.append(_item_line(item))
        lines.append("")

    lines.append("  Most mornings the right action is none. Patience is a position.")
    return "\n".join(lines)
