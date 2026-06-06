"""Ingestion (L1) — the adapters that fill the point-in-time store.

``gather_fixture_readings`` runs every adapter's offline fixture backend (the default, deterministic
path used by tests and ``--fixtures``). ``gather_live_readings`` is the opt-in ``--live`` path: it
takes the universe from the local ``positions.csv`` and pulls live prices, fund + EDGAR holdings
(so the look-through graph rebuilds from live disclosures), and the macro dial. Live backends lazily
import their heavy dependencies and are never exercised in CI.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable

from factor_scope.config import Config
from factor_scope.ingest import (
    baostock,
    calls,
    edgar,
    fred,
    fund_holdings,
    mootdx,
    positions,
    prices,
    theme_funds,
    themes,
)
from factor_scope.ingest.base import IngestError, fetched_at_for
from factor_scope.store import Reading

__all__ = ["IngestError", "gather_fixture_readings", "gather_live_readings"]

logger = logging.getLogger(__name__)

# Retry transient live-source failures (IP throttles, dropped sockets) with exponential backoff and
# full jitter — the AWS-recommended schedule that avoids a synchronised retry stampede.
_RETRY_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 1.0
_RETRY_CAP_SECONDS = 30.0

# Data circuit breaker: an isolated divergence flags-and-continues, but if more than this fraction
# of funds are unreconciled the failure is systemic (e.g. a source switched to adjusted prices) —
# fail the whole run loudly rather than ship a wall of suspect NAVs.
_DEGRADED_RUN_THRESHOLD = 0.5


def gather_fixture_readings(config: Config, *, as_of: str) -> list[Reading]:
    """Every adapter's fixture rows, stamped deterministically — the offline default."""

    fetched_at = fetched_at_for(as_of)
    root = config.fixtures_dir
    readings: list[Reading] = []
    readings += positions.load_fixture(root / positions.FIXTURE, as_of=as_of, fetched_at=fetched_at)
    readings += prices.load_fixture(root / prices.FIXTURE, fetched_at=fetched_at)
    readings += fund_holdings.load_fixture(root / fund_holdings.FIXTURE, fetched_at=fetched_at)
    readings += fred.load_fixture(root / fred.FIXTURE, fetched_at=fetched_at)
    readings += edgar.load_fixture(root / edgar.FIXTURE, fetched_at=fetched_at)
    calls_fixture = root / calls.FIXTURE
    if calls_fixture.exists():  # prior falsifiable leans for the self-scoring loop (spec §06)
        readings += calls.load_fixture(calls_fixture, fetched_at=fetched_at)
    themes_fixture = root / themes.FIXTURE
    if themes_fixture.exists():  # candidate industries for the emerging funnel (spec §07)
        readings += themes.load_fixture(themes_fixture, fetched_at=fetched_at)
    theme_funds_fixture = root / theme_funds.FIXTURE
    if theme_funds_fixture.exists():  # candidate funds the funnel screens to a top 3
        readings += theme_funds.load_fixture(theme_funds_fixture, fetched_at=fetched_at)
    return readings


def _with_retries(thunk: Callable[[], list[Reading]]) -> list[Reading]:
    """Call ``thunk``, retrying on any exception with exponential backoff + full jitter.

    Sleep before attempt *n* is ``random.uniform(0, min(cap, base·2^n))`` (full jitter), so a fleet
    of scrapers doesn't retry in lockstep. The last attempt's exception propagates to the caller.
    """

    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return thunk()
        except Exception:
            if attempt + 1 >= _RETRY_ATTEMPTS:
                raise
            ceiling = min(_RETRY_CAP_SECONDS, _RETRY_BASE_SECONDS * 2**attempt)
            time.sleep(random.uniform(0.0, ceiling))
    raise AssertionError("unreachable: _RETRY_ATTEMPTS >= 1")  # pragma: no cover


def _live_or_empty(
    fetch: Callable[..., list[Reading]], code: str, *, source: str, fetched_at: str
) -> list[Reading]:
    """A live price read that yields no rows instead of raising on failure — *loudly*.

    The CN price path is dual-sourced for anti-fragility (L1 / §04): turning one source going
    offline into an empty read lets :func:`prices.select_corroborated` fall back to the other,
    rather than letting an IP-block or timeout crash the whole nightly run. The read is retried with
    backoff first, so a transient blip is ridden out rather than counted as an outage.

    A genuine empty read (no bars today) returns ``[]`` silently; a *failure* returns ``[]`` only
    after logging the exception with its source and code, so a silently-degraded source can't go
    unnoticed for weeks — the broad ``except`` is a logged boundary, not a swallow.
    """

    try:
        return _with_retries(lambda: fetch(code, fetched_at=fetched_at))
    except Exception:
        logger.warning(
            "live price source %r failed for %s; falling back to the cross-source",
            source,
            code,
            exc_info=True,
        )
        return []


def _check_price_health(  # pragma: no cover - opt-in
    n_funds: int, degraded: list[str]
) -> None:
    """Log the per-run price-source summary and trip the data circuit breaker on systemic failure.

    ``degraded`` are the funds with no reconciled price tonight — either unpriced (both sources
    down) or flagged as a same-day divergence. An isolated case is logged for review; a systemic
    one (more than :data:`_DEGRADED_RUN_THRESHOLD` of the book) raises so the run fails loudly.
    """

    if not degraded:
        logger.info("price ingest: %d/%d funds corroborated", n_funds, n_funds)
        return
    logger.warning(
        "price ingest degraded: %d/%d funds unreconciled %s", len(degraded), n_funds, degraded
    )
    if n_funds and len(degraded) / n_funds > _DEGRADED_RUN_THRESHOLD:
        raise IngestError(
            f"prices: {len(degraded)}/{n_funds} funds unreconciled "
            f"(> {_DEGRADED_RUN_THRESHOLD:.0%}) — a price source likely broke systemically"
        )


def gather_live_readings(  # pragma: no cover - opt-in
    config: Config, *, as_of: str
) -> list[Reading]:
    """The opt-in ``--live`` path: local positions + live NAV, holdings, and the FRED macro dial.

    Each held fund's NAV *and* its latest disclosed holdings are refreshed, plus every configured
    EDGAR filer's holdings — so the connection graph rebuilds from live disclosures (spec §04/§05),
    not stale fixtures. CN prices are dual-sourced (AkShare cross-validated against Baostock) so one
    scraper going offline can't kill the run. Heavy deps stay lazily imported inside ``fetch_live``.
    """

    fetched_at = fetched_at_for(as_of)
    book = positions.load_fixture(
        config.fixtures_dir / positions.FIXTURE, as_of=as_of, fetched_at=fetched_at
    )
    readings: list[Reading] = list(book)
    degraded: list[str] = []  # funds with no reconciled price — unpriced or flagged as divergent
    for pos in book:
        # CN prices are triple-sourced: reconcile AkShare/Baostock/Mootdx to a median consensus, or
        # — if a source is offline (its read yields nothing) — fall back rather than kill the run.
        priced = prices.select_reconciled(
            [
                _live_or_empty(
                    prices.fetch_live, pos.key, source=prices.SOURCE, fetched_at=fetched_at
                ),
                _live_or_empty(
                    baostock.fetch_live, pos.key, source=baostock.SOURCE, fetched_at=fetched_at
                ),
                _live_or_empty(
                    mootdx.fetch_live, pos.key, source=mootdx.SOURCE, fetched_at=fetched_at
                ),
            ],
            tolerance=config.corroboration_tolerance,
        )
        readings += priced
        if not priced or any("divergence" in r.payload for r in priced):
            degraded.append(pos.key)
        readings += fund_holdings.fetch_live(pos.key, fetched_at=fetched_at)
    _check_price_health(len(book), degraded)
    for cik in config.edgar_ciks:
        readings += edgar.fetch_live(cik, form="NPORT-P", fetched_at=fetched_at)
    for series_id in fred.DEFAULT_SERIES:
        readings += fred.fetch_live(series_id, fetched_at=fetched_at)
    return readings
