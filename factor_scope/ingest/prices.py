"""Prices / fund-NAV adapter (CN). Fixture: `prices.csv → {code, as_of, nav}`.

Per-item gain comes from cost basis vs the current NAV pulled here. Live is AkShare's ETF history
(``fund_etf_hist_em``) — opt-in, never called in CI.
"""

from __future__ import annotations

from pathlib import Path
from statistics import median

from factor_scope.ingest.base import as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "prices"
SOURCE = "akshare"  # this adapter's provenance tag; its live backend + fixture are AkShare-shaped
FIXTURE = "prices.csv"
_REQUIRED = ("code", "as_of", "nav")

# CN prices are dual-sourced (AkShare + Baostock) so one scraper going offline can't kill a run.
# Two same-day reads within this fraction corroborate each other; the default is the
# SEC/CSSF NAV-error materiality baseline (0.5%). Callers pass a per-run override (Config).
_CORROBORATION_TOLERANCE = 0.005


def parse(text: str, *, fetched_at: str) -> list[Reading]:
    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        code = required_str(row, "code", line_no, SERIES)
        as_of = required_str(row, "as_of", line_no, SERIES)
        nav = as_float(row, "nav", line_no, SERIES)
        readings.append(
            Reading(
                series=SERIES,
                key=code,
                as_of=as_of,
                fetched_at=fetched_at,
                payload={"nav": nav, "source": SOURCE},
            )
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)


def _flag(reading: Reading, peer_nav: float) -> Reading:
    """Annotate a reading with the unreconciled peer NAV — a quality flag, not a failure."""

    return reading.model_copy(update={"payload": {**reading.payload, "divergence": peer_nav}})


def select_reconciled(
    reads: list[list[Reading]], *, tolerance: float = _CORROBORATION_TOLERANCE
) -> list[Reading]:
    """Reconcile one fund's NAV across the CN price sources, in priority order (AkShare first).

    ``reads`` is each source's read (possibly empty), AkShare → Baostock → Mootdx. The CN path is
    multi-sourced for anti-fragility; this is the selection policy, robust to one bad
    source and never fatal:

    - **Fall back** — a source that is offline contributes an empty read; only the sources that
      returned a same-day bar are reconciled, so a blocked scraper can't kill the run.
    - **One source** — nothing to cross-check, so it is returned as-is.
    - **Two sources** — corroborate within ``tolerance``; on a material gap keep the priority
      (AkShare) value but annotate it with the peer NAV (``payload["divergence"]``).
    - **Three+ sources** — the **median** is the consensus (robust to a single outlier, *including*
      a bad AkShare); the returned reading is the real source carrying the median value, flagged
      with the most-divergent peer when any source falls outside ``tolerance``.

    Reconciliation is gated on a matching ``as_of`` (only the freshest day's reads are compared), so
    a stale source is ignored rather than spuriously flagged. All sources read the same unadjusted,
    raw-close basis (AkShare ``adjust=""``, Baostock ``adjustflag="3"``, Mootdx default), so a
    split/dividend adjustment can't manufacture a divergence.
    """

    present = [r[-1] for r in reads if r]
    if not present:
        return []
    latest = max(p.as_of for p in present)
    cohort = [p for p in present if p.as_of == latest]
    if len(cohort) == 1:
        return [cohort[0]]

    navs = [p.payload["nav"] for p in cohort]
    if len(cohort) == 2:
        a, b = navs
        if a and abs(a - b) / abs(a) > tolerance:
            return [_flag(cohort[0], b)]  # keep the priority source, flag the disagreement
        return [cohort[0]]

    consensus = median(navs)  # odd-N median is a real source's value → honest provenance
    canonical = min(cohort, key=lambda p: abs(p.payload["nav"] - consensus))
    outliers = [n for n in navs if consensus and abs(n - consensus) / abs(consensus) > tolerance]
    if outliers:
        return [_flag(canonical, max(outliers, key=lambda n: abs(n - consensus)))]
    return [canonical]


def fetch_live(code: str, *, fetched_at: str) -> list[Reading]:  # pragma: no cover - opt-in
    """Pull the latest daily NAV for one ETF via AkShare. Requires the `live` extra + network."""

    import akshare as ak

    frame = ak.fund_etf_hist_em(symbol=code, period="daily", adjust="")  # adjust="" → raw close
    last = frame.iloc[-1]
    return [
        Reading(
            series=SERIES,
            key=code,
            as_of=str(last["日期"]),
            fetched_at=fetched_at,
            payload={"nav": float(last["收盘"]), "source": SOURCE},
        )
    ]
