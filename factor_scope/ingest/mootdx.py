"""Prices adapter (CN, Mootdx) — the third, independent source for the ``prices`` series.

CN ingestion is dual/triple-sourced (AkShare + Baostock + Mootdx) so one scraper being
IP-blocked or offline never kills a nightly run. This module is the Mootdx leg: a thin
``fetch_live`` that reads the latest daily close for one ETF over the TDX (通达信) market protocol,
stamped into the same ``prices`` :class:`~factor_scope.store.Reading` shape as the other two legs.
With three sources, :func:`factor_scope.ingest.prices.select_reconciled` takes the **median** — so a
single bad source (including the AkShare primary) can no longer poison the NAV.

The TDX protocol is the one leg with no built-in socket timeout and a per-call *server selection*
step (mootdx probes dozens of candidate hosts), which together can wedge a cold-start full-universe
run indefinitely. So this leg is hardened: a **pinned** server (selection never runs), an explicit
**socket timeout** bounding connect + every read, and the library's internal **auto-retry disabled**
(we own retries at the resilience boundary). A dead pin is dropped and the next host tried.

Like every live backend, the heavy dependency is imported lazily inside the call so the core
installs and CI run offline; ``fetch_live`` is the default live path and never called in CI (which
forces offline).
"""

from __future__ import annotations

from typing import Any

from factor_scope.ingest.base import IngestError
from factor_scope.ingest.prices import _SEED_TRADING_DAYS, SERIES
from factor_scope.store import Reading

SOURCE = "mootdx"  # this adapter's provenance tag
_DAILY = 9  # Mootdx frequency code for the daily K-line; bars are unadjusted (raw close)

# A hard socket timeout (connect + every recv) — pytdx/tdxpy exposes none by default, so a silent
# TDX server would otherwise block a read forever. Small enough that a bounded read finishes well
# within the per-read deadline (``_with_timeout``), so the leg degrades rather than hangs.
_SOCKET_TIMEOUT_SECONDS = 8

# The TDX server pinned for this run, picked once and cached so we never re-run mootdx's per-call
# (asyncio) server-selection probe across dozens of hosts — both a per-call cost and a hang surface.
# A read failure drops the pin and advances ``_server_index`` so the next attempt rotates hosts.
_server: tuple[str, int] | None = None
_server_index = 0


def _pinned_server() -> tuple[str, int]:
    """The cached ``(ip, port)`` to connect to — a host from mootdx's bundled list, picked once.

    Pinning means construction is a single bounded TCP connect (``server=`` + ``bestip=False``),
    not a fan-out of candidate probes. The index advances on each :func:`_reset_server`, so repeated
    failures walk the host list rather than wedging on one dead server.
    """

    global _server
    if _server is None:
        from mootdx.consts import HQ_HOSTS

        _name, ip, port = HQ_HOSTS[_server_index % len(HQ_HOSTS)]
        _server = (ip, int(port))
    return _server


def _reset_server() -> None:
    """Drop the current pin and rotate to the next candidate host on the next pick."""

    global _server, _server_index
    _server = None
    _server_index += 1


def fetch_live(code: str, *, fetched_at: str, since: str | None = None) -> list[Reading]:
    """Pull one ETF's daily-close history via Mootdx. Requires the `live` extra + network.

    Returns the same windowed/incremental contract as the other two legs: a ~400-bar window (Mootdx
    is count-based over the TDX protocol, not date-ranged), then only sessions past the watermark
    are kept — so the price series is corroborated across the full window, not just the latest bar.

    Construction pins a server and bounds the socket; the library's auto-retry is disabled (the
    resilience boundary owns retries). A silent server (``None`` frame) is raised, not swallowed, so
    the boundary degrades the leg and the next attempt rotates to another host; the per-call client
    is always closed.
    """

    from mootdx.quotes import Quotes

    ip, port = _pinned_server()
    client: Any = None
    try:
        client = Quotes.factory(
            market="std", server=(ip, port), bestip=False, timeout=_SOCKET_TIMEOUT_SECONDS
        )
        # auto_retry off: tdxpy's reconnect+resend loop multiplies the deadline on a dead host
        client.client.auto_retry = False
        frame = client.bars(symbol=code, frequency=_DAILY, offset=_SEED_TRADING_DAYS)
        if frame is None:  # a blocked/failed call (distinct from an empty frame) — degrade + rotate
            raise IngestError(f"mootdx: no response from {ip}:{port} for {code}")
    except Exception:
        _reset_server()
        raise
    finally:
        if client is not None:
            client.close()

    if frame.empty:  # unknown/delisted code → no data, so the caller falls back to the other legs
        return []
    return [
        Reading(
            series=SERIES,
            key=code,
            as_of=str(index)[:10],
            fetched_at=fetched_at,
            payload={"nav": float(row["close"]), "source": SOURCE},
        )
        for index, row in frame.iterrows()
        if since is None or str(index)[:10] > since
    ]
