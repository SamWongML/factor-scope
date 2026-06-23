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

from factor_scope.ingest.base import EASTMONEY_KLINE, IngestError, host_breaker
from factor_scope.store import Reading

__all__ = ["IngestDeadline", "IngestError"]

logger = logging.getLogger(__name__)

# Retry transient live-source failures (IP throttles, dropped sockets) with exponential backoff and
# full jitter — the AWS-recommended schedule that avoids a synchronised retry stampede.
_RETRY_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 1.0
_RETRY_CAP_SECONDS = 30.0

# Per-attempt wall-clock deadline — a backstop above each adapter's own socket timeout (the Mootdx
# leg sets one explicitly). Each retry gets a fresh deadline, so the worst-case time per source is
# bounded by _RETRY_ATTEMPTS × this + backoff.
_TIMEOUT_SECONDS = 20.0

# Whole-universe snapshot reads (fund membership + the ETF-scale board) aggregate several large
# AkShare pulls and legitimately take ~30s — well past the per-fund deadline above — so they get a
# wider one (:func:`_bounded`) that still catches a true hang but won't fail a slow working pull.
_UNIVERSE_TIMEOUT_SECONDS = 120.0

# Data circuit breaker: an isolated divergence flags-and-continues, but if more than this fraction
# of funds are unreconciled the failure is systemic (e.g. a source switched to adjusted prices) —
# fail the whole run loudly rather than ship a wall of suspect NAVs.
_DEGRADED_RUN_THRESHOLD = 0.5


class IngestDeadline:
    """An overall wall-clock budget for one ingest run — the run-level backstop above the per-read
    deadline (:func:`_with_timeout`). It bounds the *whole* gather so no single wedged leg can
    stall a nightly run indefinitely.

    ``seconds`` is the budget measured from run start; ``None`` or any non-positive value (the
    default everywhere but an explicit ``--deadline``) means unbounded — mirroring
    ``live_pacing_seconds``'s "0 disables" convention, so ``--deadline 0`` reads as "no cap", not
    "stop immediately" — so the offline suite and the byte-for-byte artifact stay unaffected.
    Checked at the top of the per-fund/per-code ingest loops; on expiry the loop stops and the
    partial-but-valid readings gathered so far still ship. ``clock`` is injectable for tests; it is
    wall-clock time used only for control flow, never written into the artifact.
    """

    def __init__(
        self, seconds: float | None, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        # A non-positive budget collapses to unbounded (like live_pacing_seconds's "0 disables"), so
        # ``--deadline 0`` means "no cap" rather than "stop on the first check".
        self._budget = seconds if (seconds is not None and seconds > 0) else None
        self._clock = clock
        self._start = clock()

    def exceeded(self) -> bool:
        """True once elapsed wall-clock has reached the budget (never, when unbounded)."""

        return self._budget is not None and (self._clock() - self._start) >= self._budget


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
    """Bound a blocking source read with a wall-clock deadline — the per-read backstop.

    The read runs on a daemon thread and we wait at most ``seconds``. Python cannot kill a thread,
    so on a timeout the worker is *abandoned* — but it is a daemon, so it never blocks process exit
    — and a :class:`TimeoutError` propagates to the retry/fallback boundary. Ingestion is
    sequential, so at most one such thread can leak at a time.

    This is a backstop: the adapters that wrap raw sockets set their own connect/read socket timeout
    (e.g. the Mootdx/TDX leg), so a read self-bounds and the abandoned-thread case is the exception,
    not the rule. The run-level :class:`IngestDeadline` caps the whole gather above this.
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


def _bounded(thunk: Callable[[], list[Reading]]) -> list[Reading]:
    """Bound a critical, no-safe-empty live read: retry + a per-attempt wall-clock deadline, but
    *propagate* on persistent failure rather than swallowing to ``[]``.

    Unlike :func:`_live_or_empty`, the result is not degraded to an empty read. It is for the reads
    that have no safe empty fallback — the whole-universe membership and the ETF-scale board, which
    are called once before the per-fund loop and feed delisting detection and the tier screen.
    A hung host on one of those is converted from an unbounded stall (the run-level deadline can't
    fire on a synchronous call) into a bounded, loud failure, rather than a silently empty universe.
    The deadline is :data:`_UNIVERSE_TIMEOUT_SECONDS` (wider than the per-fund one), since these
    whole-market pulls legitimately take ~30s — the per-fund 20s would fail a working pull.
    """

    return _with_retries(lambda: _with_timeout(thunk, _UNIVERSE_TIMEOUT_SECONDS))


def _live_or_empty(
    fetch: Callable[..., list[Reading]],
    code: str,
    *,
    source: str,
    fetched_at: str,
    **kwargs: object,
) -> list[Reading]:
    """A live read that yields no rows instead of raising on failure — *loudly*.

    The live ingest path is resilient by construction: turning a source going offline into an empty
    read lets the caller degrade — the multi-source price path reconciles to the remaining sources
    (:func:`prices.select_reconciled`), a per-fund factor leg falls to ``FactorState(valid=False)``
    — rather than letting an IP-block, hang, or timeout crash the whole nightly run. Each attempt is
    bounded by a wall-clock deadline and retried with backoff, so a transient blip is ridden out and
    a hung server is abandoned rather than counted as an outage. Any ``kwargs`` (e.g. the
    incremental-fetch ``since`` watermark) pass straight through to ``fetch``.

    A genuine empty read (no bars today) returns ``[]`` silently; a *failure* returns ``[]`` only
    after logging the exception with its source and code, so a silently-degraded source can't go
    unnoticed for weeks — the broad ``except`` is a logged boundary, not a swallow.
    """

    try:
        return _with_retries(
            lambda: _with_timeout(
                lambda: fetch(code, fetched_at=fetched_at, **kwargs), _TIMEOUT_SECONDS
            )
        )
    except Exception:
        logger.warning(
            "live source %r failed for %s; degrading to no reading this run",
            source,
            code,
            exc_info=True,
        )
        return []


def _check_price_health(n_funds: int, degraded: list[str]) -> None:
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


def _check_eastmoney_health() -> None:
    """Surface one run-level alarm when the EastMoney K-line host blocked the run.

    The per-host breaker collapses a blocked host into a single summary line instead of the wall of
    per-call warnings a full-universe burst would emit — the price/activity legs that fell back are
    already individually degraded; this is the run-level signal an operator scans each morning. The
    block is transient (an IP cooldown), so it is a warning, not a failure: the artifact is still
    valid, the trend/crowding surfaces provisional for the affected funds until the host clears.
    """

    if host_breaker.is_open(EASTMONEY_KLINE):
        logger.warning(
            "ingest: EastMoney K-line host blocked this run after %d refusals — price/activity "
            "legs fell back to Sina/the spot board; those funds' trend/crowding surfaces are "
            "provisional until the IP cooldown passes",
            host_breaker.failures(EASTMONEY_KLINE),
        )
