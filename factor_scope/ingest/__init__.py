"""Ingestion — per-source adapters plus the shared resilience infra they run behind.

Each adapter (a submodule here) turns one source into stamped :class:`~factor_scope.store.Reading`
rows via a ``fetch_live`` backend (the online default; heavy deps lazily imported inside the call)
and a ``load_fixture`` backend (the offline test mode). The :mod:`factor_scope.markets` layer
composes the adapters into a market; the live multi-source price path reuses the resilience helpers
below — bounded retries
(:func:`_with_retries`), a per-read wall-clock deadline (:func:`_with_timeout`), a failover wrapper
(:func:`_live_or_empty`), and the data circuit breaker (:func:`_check_price_health`).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable

from factor_scope.ingest.base import IngestError
from factor_scope.store import Reading

__all__ = ["IngestError"]

logger = logging.getLogger(__name__)

# Retry transient live-source failures (IP throttles, dropped sockets) with exponential backoff and
# full jitter — the AWS-recommended schedule that avoids a synchronised retry stampede.
_RETRY_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 1.0
_RETRY_CAP_SECONDS = 30.0

# Per-attempt wall-clock deadline. The CN scraper libraries wrap sockets and expose no timeout, so a
# hung server could otherwise stall the whole nightly run. Each retry gets a fresh deadline, so the
# worst-case time per source is bounded by _RETRY_ATTEMPTS × this + backoff.
_TIMEOUT_SECONDS = 20.0

# Data circuit breaker: an isolated divergence flags-and-continues, but if more than this fraction
# of funds are unreconciled the failure is systemic (e.g. a source switched to adjusted prices) —
# fail the whole run loudly rather than ship a wall of suspect NAVs.
_DEGRADED_RUN_THRESHOLD = 0.5


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


def _with_timeout(thunk: Callable[[], list[Reading]], seconds: float) -> list[Reading]:
    """Bound a blocking source read with a wall-clock deadline.

    The CN scraper libraries wrap sockets and expose no timeout, so a hung server could otherwise
    stall the whole nightly run. The read runs on a daemon thread and we wait at most ``seconds``.
    Python cannot kill a thread, so on a timeout the worker is *abandoned* — but it is a daemon, so
    it never blocks process exit — and a :class:`TimeoutError` propagates to the retry/fallback
    boundary. Ingestion is sequential, so at most one such thread can leak at a time.
    """

    result: list[list[Reading]] = []
    error: list[BaseException] = []

    def run() -> None:
        try:
            result.append(thunk())
        except BaseException as exc:  # carry the worker's failure back to the calling thread
            error.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise TimeoutError(f"live read exceeded {seconds}s")
    if error:
        raise error[0]
    return result[0]


def _live_or_empty(
    fetch: Callable[..., list[Reading]], code: str, *, source: str, fetched_at: str
) -> list[Reading]:
    """A live price read that yields no rows instead of raising on failure — *loudly*.

    The CN price path is multi-sourced for anti-fragility: turning one source going
    offline into an empty read lets :func:`prices.select_reconciled` fall back to the others, rather
    than letting an IP-block, hang, or timeout crash the whole nightly run. Each attempt is bounded
    by a wall-clock deadline and the read is retried with backoff, so a transient blip is ridden out
    and a hung server is abandoned rather than counted as an outage.

    A genuine empty read (no bars today) returns ``[]`` silently; a *failure* returns ``[]`` only
    after logging the exception with its source and code, so a silently-degraded source can't go
    unnoticed for weeks — the broad ``except`` is a logged boundary, not a swallow.
    """

    try:
        return _with_retries(
            lambda: _with_timeout(lambda: fetch(code, fetched_at=fetched_at), _TIMEOUT_SECONDS)
        )
    except Exception:
        logger.warning(
            "live price source %r failed for %s; falling back to the cross-source",
            source,
            code,
            exc_info=True,
        )
        return []


def _check_price_health(  # pragma: no cover - live path
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
