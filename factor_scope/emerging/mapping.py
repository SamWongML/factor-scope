"""Inferred theme→fund mapping — candidate generation from data (重合度 + 涨跌幅相关性).

A professional CN sector-rotation desk (ROADMAP §4) never starts from a hand-tagged table: it
derives each theme's candidate funds from the data. For a theme's reference constituents, this
module ranks the fund universe by **holdings overlap (重合度)** — the disclosed weight a fund
carries in the theme's names, read off the look-through graph — **confirmed by rolling return
correlation (涨跌幅相关性)**: a fund that both holds the names *and* co-moves with them is a
genuine pure-play, not a loose label.

Overlap is the dominant signal (the holdings *are* the exposure); correlation only confirms it,
so a name held but not co-moving gets partial credit. When a fund's price history is too short to
correlate the read degrades to overlap alone rather than dropping the fund — invalid inputs
degrade, never raise. The combining constants carry economic meaning and are never tuned to P&L;
this is transparent candidate *generation*, not a fitted composite of the judgment factors.
Deterministic given a snapshot.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from factor_scope.graph.lookthrough import overlap_with
from factor_scope.graph.store import GraphStore
from factor_scope.store import PointInTimeStore

__all__ = [
    "CONFIRM_FLOOR",
    "CORR_MIN_POINTS",
    "CORR_WINDOW",
    "MIN_OVERLAP",
    "ThemeFundLink",
    "infer_links",
    "return_correlation",
]

# Economic-meaning constants (never tuned to P&L):
MIN_OVERLAP = 0.10  # a fund must carry ≥10% disclosed weight in the theme's names to be a candidate
CORR_WINDOW = 60  # the rolling window (trading days) the return correlation is measured over
CORR_MIN_POINTS = 20  # fewer aligned return points than this → correlation degrades to None
CONFIRM_FLOOR = 0.5  # overlap kept absent co-movement confirmation (full credit when correlation→1)


@dataclass(frozen=True)
class ThemeFundLink:
    """One inferred theme→fund edge: the overlap, the correlation confirming it, the score."""

    theme: str
    code: str
    overlap: float  # 重合度 ∈ [0,1]: Σ the fund's disclosed weight in the theme's constituents
    overlap_names: tuple[str, ...]  # the constituent names the fund actually holds (sorted)
    correlation: float | None  # 涨跌幅相关性 ∈ [-1,1]; None when price history is too short to read
    score: float  # pure-play confidence ∈ [0,1]: overlap, confirmed (or discounted) by correlation


def _dated_navs(store: PointInTimeStore, code: str, as_of: str) -> list[tuple[str, float]]:
    """A code's point-in-time NAVs as ``(date, nav)``, oldest-first, one per date (latest wins)."""

    by_date: dict[str, float] = {}
    for r in sorted(store.history("prices", code), key=lambda r: (r.as_of, r.fetched_at)):
        if r.as_of <= as_of:
            by_date[r.as_of] = float(r.payload["nav"])  # later fetched_at for a date wins
    return sorted(by_date.items())


def _returns(dated: list[tuple[str, float]]) -> dict[str, float]:
    """Daily simple returns keyed by date, from an oldest-first ``(date, nav)`` series."""

    return {
        date: nav / prev - 1.0
        for (_, prev), (date, nav) in zip(dated, dated[1:], strict=False)
        if prev
    }


def _index_returns(
    store: PointInTimeStore, constituents: Sequence[str], as_of: str
) -> dict[str, float]:
    """The equal-weight reference index's daily returns: the mean constituent return per date."""

    per_name = [_returns(_dated_navs(store, c, as_of)) for c in constituents]
    priced = [r for r in per_name if r]
    if not priced:
        return {}
    dates: set[str] = set().union(*priced)
    index: dict[str, float] = {}
    for date in dates:
        day = [r[date] for r in priced if date in r]
        index[date] = sum(day) / len(day)
    return index


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation, or ``None`` when either series has no variance (undefined)."""

    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0.0 or vy == 0.0:
        return None
    return cov / float((vx * vy) ** 0.5)


def return_correlation(
    store: PointInTimeStore,
    code: str,
    as_of: str,
    constituents: Sequence[str],
    *,
    window: int = CORR_WINDOW,
) -> float | None:
    """Rolling return correlation of a fund vs the theme's equal-weight index (涨跌幅相关性).

    Correlates the fund's daily NAV returns with the reference index built from the constituents'
    priced series, over the most recent ``window`` dates both share. ``None`` when fewer than
    :data:`CORR_MIN_POINTS` dates align (a fund or its constituents lack the price history) — the
    read degrades rather than raising, and the mapping leans on overlap alone.
    """

    fund = _returns(_dated_navs(store, code, as_of))
    index = _index_returns(store, constituents, as_of)
    common = sorted(set(fund) & set(index))[-window:]
    if len(common) < CORR_MIN_POINTS:
        return None
    return _pearson([fund[d] for d in common], [index[d] for d in common])


def _score(overlap: float, correlation: float | None) -> float:
    """Overlap, confirmed by co-movement: full credit as correlation→1, :data:`CONFIRM_FLOOR` floor.

    Overlap stays the dominant ranking signal; an unmeasurable (``None``) correlation keeps full
    overlap (absence of evidence is not evidence of absence), while a measured non-co-mover is
    discounted toward the floor.
    """

    if correlation is None:
        return overlap
    return overlap * (CONFIRM_FLOOR + (1.0 - CONFIRM_FLOOR) * max(0.0, correlation))


def infer_links(
    constituents_by_theme: Mapping[str, Sequence[str]],
    fund_codes: Iterable[str],
    graph: GraphStore,
    store: PointInTimeStore,
    as_of: str,
    *,
    min_overlap: float = MIN_OVERLAP,
) -> list[ThemeFundLink]:
    """Rank each theme's funds by overlap (confirmed by correlation); drop those below the floor.

    Themes are processed in name order and each theme's funds ranked by ``(-score, code)``, so the
    mapping is deterministic given a snapshot regardless of input order. A fund can map to several
    themes and a theme to several funds (one-to-one / one-to-many / many-to-one / many-to-many).
    """

    codes = list(fund_codes)
    links: list[ThemeFundLink] = []
    for theme in sorted(constituents_by_theme):
        constituents = constituents_by_theme[theme]
        members = set(constituents)
        theme_links: list[ThemeFundLink] = []
        for code in codes:
            overlap, names = overlap_with(graph, code, as_of, members.__contains__)
            if overlap < min_overlap:
                continue
            correlation = return_correlation(store, code, as_of, constituents)
            theme_links.append(
                ThemeFundLink(
                    theme=theme,
                    code=code,
                    overlap=round(overlap, 6),
                    overlap_names=tuple(names),
                    correlation=None if correlation is None else round(correlation, 6),
                    score=round(_score(overlap, correlation), 6),
                )
            )
        theme_links.sort(key=lambda link: (-link.score, link.code))
        links.extend(theme_links)
    return links
