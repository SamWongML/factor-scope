"""EastMoney daily K-line client — a browser-fingerprinted fetch that defeats the push2his reset.

EastMoney's history host (``push2his.eastmoney.com``) drops a plain ``requests`` connection: the
TLS/HTTP fingerprint of a non-browser client is refused mid-handshake. This client speaks the same
``kline/get`` endpoint through ``curl_cffi`` impersonating a real Chrome (matching TLS + HTTP/2
fingerprint), a fresh session per call, and a browser ``Referer``, so the daily window comes back
instead of a dropped socket.

It returns domain-keyed bars ``{date, close, turnover, amount}`` — one call carries both the price
leg (``close`` → NAV) and the trading-activity leg (``turnover`` / ``amount``); only the NAV leg is
wired today. On a connection/read error it makes one jittered retry then raises, so the caller's
existing circuit-breaker / Sina-fallback / per-read-deadline boundary drives the degradation.
"""

from __future__ import annotations

import random
import time
from typing import Any

# The endpoint and fixed query contract replicated from the installed ``akshare`` source — the
# params its working ``fund_etf_hist_em`` pull sends. ``fields2`` is the column order of each
# ``data.klines`` CSV row: date,open,close,high,low,volume,amount,amplitude,pct,chg,turnover.
_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_FIELDS1 = "f1,f2,f3,f4,f5,f6"
_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116"
_UT = "7eea3edcaed734bea9cbfc24409ed989"
_KLT_DAILY = "101"
_FQT_UNADJUSTED = "0"
_END = "20500101"  # the contract's open-ended upper bound; the window is bounded below by ``beg``
# The browser referer push2his keys its block on; the impersonate profile supplies the User-Agent.
_REFERER = "https://quote.eastmoney.com/"

# One jittered retry on a transport blip (2 attempts total), then raise — full jitter so a
# full-universe loop doesn't retry in lockstep. Live-only; never on the deterministic artifact path.
_RETRY_BACKOFF_SECONDS = 1.0


def kline(code: str, *, beg: str, impersonate: str = "chrome") -> list[dict[str, str | float]]:
    """Fetch ``code``'s daily K-line from ``beg`` (``YYYYMMDD``) as domain bars, oldest-first.

    ``secid`` encodes the listing exchange by code prefix (``5x`` → SSE market 1, else SZSE 0),
    matching the Sina / Baostock legs. An empty ``data`` block (a delisted/unknown code) yields
    ``[]`` rather than raising.
    """

    from curl_cffi import requests

    params = {
        "fields1": _FIELDS1,
        "fields2": _FIELDS2,
        "ut": _UT,
        "klt": _KLT_DAILY,
        "fqt": _FQT_UNADJUSTED,
        "beg": beg,
        "end": _END,
        "secid": f"{1 if code.startswith('5') else 0}.{code}",
    }
    block = _fetch(requests, params, impersonate).get("data")
    if not block:
        return []
    return [_bar(line) for line in block["klines"]]


def _fetch(requests: Any, params: dict[str, str], impersonate: str) -> dict[str, Any]:
    """GET the endpoint behind a fresh impersonating session; one jittered retry then raise."""

    for attempt in range(2):
        try:
            session = requests.Session(impersonate=impersonate)
            payload: dict[str, Any] = session.get(
                _URL, params=params, headers={"Referer": _REFERER}
            ).json()
            return payload
        except requests.RequestsError:
            if attempt == 1:
                raise
            time.sleep(random.uniform(0.0, _RETRY_BACKOFF_SECONDS))
    raise AssertionError("unreachable: range(2) always returns or raises")  # pragma: no cover


def _bar(line: str) -> dict[str, str | float]:
    """One ``data.klines`` CSV row → a domain bar; columns are AkShare's ``fields2`` order."""

    cells = line.split(",")
    return {
        "date": cells[0],
        "close": float(cells[2]),
        "amount": float(cells[6]),
        "turnover": float(cells[10]),
    }
