"""Render a :class:`~factor_scope.contract.Dashboard` to a readable terminal summary (L6).

This is the human review surface: ``factor-scope run`` prints it so the current state of the
engine is reviewable at every phase. It degrades gracefully — fields not yet produced by the
current phase simply read as "—".
"""

from __future__ import annotations

from factor_scope.contract import Dashboard, DashboardItem, ListName

_LIST_TITLES = {
    ListName.HOLDINGS: "HOLDINGS  (hold · trim · exit?)",
    ListName.WATCHLIST: "WATCHLIST  (buy-early trigger?)",
    ListName.EMERGING: "EMERGING  (promote a weak signal?)",
}


def _item_line(item: DashboardItem) -> str:
    lean = item.lean.text if item.lean else "—"
    conf = f"{item.lean.confidence:.2f}" if item.lean else "—"
    gain = f"{item.gain:+.1%}" if item.gain is not None else "—"
    gate = item.gate.value
    flag = " ⚠connections" if item.connections_flag else ""
    evidence = item.evidence[0].one_line if item.evidence else "—"
    return (
        f"    • {item.item}  (gain {gain})\n"
        f"        lean: {lean}  (conf {conf})  gate: {gate}{flag}\n"
        f"        evidence: {evidence}"
    )


def render(dash: Dashboard) -> str:
    """Return the morning artifact as plain text."""

    lines: list[str] = []
    lines.append("◆ WEALTH-ASSISTANT ENGINE — morning artifact")
    lines.append(f"  as_of: {dash.as_of}   generated_at: {dash.generated_at}")
    lines.append(f"  items: {len(dash.items)}   (schema v{dash.schema_version})")
    lines.append("")

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
