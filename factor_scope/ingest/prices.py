"""Prices / fund-NAV adapter (CN). Reads ``{code, as_of, nav}`` rows.

Per-item gain comes from cost basis vs the current NAV pulled here, and the trend/reversal/low-vol
factors read the NAV history. Live, EastMoney's daily K-line is fetched through the
browser-fingerprinted :mod:`~factor_scope.ingest.eastmoney` client (which defeats the ``push2his``
reset), with a Sina (``fund_etf_hist_sina``) fallback for when that host refuses the request — never
called in CI (which forces offline). Offline, the recorded NAV history is replayed through the feed
and reconciled across the three corroborating legs by :func:`select_reconciled`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import timedelta
from statistics import median
from typing import Any

from factor_scope.ingest import eastmoney
from factor_scope.ingest.base import EASTMONEY_KLINE, day_after, host_breaker, run_date
from factor_scope.store import Reading

logger = logging.getLogger(__name__)

SERIES = "prices"
SOURCE = "akshare"  # this adapter's provenance tag; its live backend is AkShare-shaped

# CN prices are dual-sourced (AkShare + Baostock) so one scraper going offline can't kill a run.
# Two same-day reads within this fraction corroborate each other; the default is the
# SEC/CSSF NAV-error materiality baseline (0.5%). Callers pass a per-run override (Config).
_CORROBORATION_TOLERANCE = 0.005

# The cold-start seed window: the trend gate's 200-day MA (the one hard cap) plus a warm-up margin —
# ~400 trading days, so the gate ranks against a stable own-history distribution from night one
# rather than accruing one bar a night for ~200 nights. The A-share calendar runs ~243 trading
# days/year, so ~400 trading days spans ~650 calendar days once holiday clusters are counted; the
# EastMoney/Sina date range is calendar, so we ask for the wider calendar span and keep what lands.
_SEED_TRADING_DAYS = 400
_SEED_CALENDAR_DAYS = 650

# EastMoney's full-history floor — the ``beg`` used when there is no watermark and no real run date
# to seed a window from (the unit fakes pass a sentinel stamp), matching AkShare's default start.
_HISTORY_EPOCH = "19700101"


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


def fetch_live(
    code: str, *, fetched_at: str, since: str | None = None, impersonate: str = "chrome"
) -> list[Reading]:
    """Pull one ETF's daily raw-close NAV history. Requires `live` + network.

    Returns a *window* of bars, not just the latest: the trend/reversal/low-vol factors read the
    full stored NAV history, so the cold (``since`` is None) pull seeds ~400 trading days and each
    later night pulls only the sessions after the watermark — the same incremental contract the
    other per-fund series use, turning the nightly re-pull from quadratic to linear.

    EastMoney is the primary backend, fetched through the browser-fingerprinted :mod:`eastmoney`
    K-line client (``impersonate`` profile) that defeats the ``push2his`` reset; when that host
    refuses the request, Sina serves the same unadjusted daily close, so a block on one host can't
    unprice the book. The provenance tag stays ``akshare`` either way — both are on the same
    raw-close basis Sina's AkShare backend reads.
    """

    floor = _floor(fetched_at, since)
    if host_breaker.is_open(EASTMONEY_KLINE):
        return _sina(code, fetched_at=fetched_at, floor=floor)  # host known-blocked → skip it
    try:
        rows = eastmoney.kline(code, beg=_em_start(fetched_at, since), impersonate=impersonate)
        bars = [{"as_of": bar["date"], "nav": bar["close"]} for bar in rows]
        host_breaker.record_success(EASTMONEY_KLINE)
    except Exception as exc:
        host_breaker.record_failure(EASTMONEY_KLINE)
        logger.warning("prices: EastMoney history refused %s (%s); falling back to Sina", code, exc)
        return _sina(code, fetched_at=fetched_at, floor=floor)
    return _to_readings(code, bars, fetched_at=fetched_at, floor=floor)


def _sina(code: str, *, fetched_at: str, floor: str | None) -> list[Reading]:
    """The Sina fallback: full history (no date range), trimmed client-side to the same window."""

    import akshare as ak

    frame = ak.fund_etf_hist_sina(symbol=_sina_symbol(code))
    bars = ({"as_of": str(r["date"]), "nav": float(r["close"])} for _, r in frame.iterrows())
    return _to_readings(code, bars, fetched_at=fetched_at, floor=floor)


def _em_start(fetched_at: str, since: str | None) -> str:
    """EastMoney's ``beg`` (``YYYYMMDD``): watermark+1 incrementally, else the cold-seed floor.

    Degrades to the full-history epoch when there is no watermark and the run stamp is not a real
    date (the unit fakes) — an unbounded window rather than a raise.
    """

    if since is not None:
        return day_after(since).strftime("%Y%m%d")
    anchor = run_date(fetched_at)
    if anchor is None:
        return _HISTORY_EPOCH
    return (anchor - timedelta(days=_SEED_CALENDAR_DAYS)).strftime("%Y%m%d")


def _floor(fetched_at: str, since: str | None) -> str | None:
    """The client-side lower bound (``YYYY-MM-DD``) bars must clear — kept for the Sina path.

    EastMoney honours ``beg`` server-side, but the Sina fallback always returns full history, so the
    same window is enforced here: strictly past the watermark incrementally, else the seed floor (or
    ``None`` — no bound — when the run stamp is not a real date).
    """

    if since is not None:
        return since
    anchor = run_date(fetched_at)
    return (anchor - timedelta(days=_SEED_CALENDAR_DAYS)).isoformat() if anchor else None


def _to_readings(
    code: str, bars: Iterable[Mapping[str, Any]], *, fetched_at: str, floor: str | None
) -> list[Reading]:
    """Map ``{as_of, nav}`` bars to Readings, keeping only those strictly past the window floor."""

    return [
        Reading(
            series=SERIES,
            key=code,
            as_of=str(bar["as_of"]),
            fetched_at=fetched_at,
            payload={"nav": float(bar["nav"]), "source": SOURCE},
        )
        for bar in bars
        if floor is None or str(bar["as_of"]) > floor
    ]


def _sina_symbol(code: str) -> str:
    """AkShare's Sina ETF symbol — the exchange-prefixed code the Sina endpoint expects.

    SSE-listed ETFs use codes in the ``5`` range, SZSE-listed ones in the ``1`` range.
    """

    return f"{'sh' if code.startswith('5') else 'sz'}{code}"
