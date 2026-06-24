"""Unit tests for the EastMoney daily K-line client at the ``curl_cffi`` session boundary.

These pin the request contract replicated from the installed ``akshare`` source (params / ``secid``
/ ``beg`` / ``end`` / ``klt`` / ``fqt``), the ``data.klines`` → domain-bar parsing, the
empty-``data`` degrade, and the one-retry-then-raise transport policy — without touching the
network: a fake ``curl_cffi.requests`` module records each session + GET and serves a canned body.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from factor_scope.ingest import eastmoney

pytestmark = pytest.mark.unit


class _FakeRequestsError(OSError):
    """Stands in for ``curl_cffi.requests.RequestsError`` — its connection/read transport base."""


def install_fake_curl_cffi(
    monkeypatch: Any, *, payload: Any = None, fail_times: int = 0
) -> dict[str, list[Any]]:
    """Inject a network-free ``curl_cffi`` whose ``Session`` records its impersonate + each GET.

    ``payload`` is the JSON each successful GET returns; the first ``fail_times`` GETs raise the
    transport error instead, so the retry policy can be exercised deterministically.
    """

    calls: dict[str, list[Any]] = {"sessions": [], "gets": []}

    class _Response:
        def __init__(self, body: Any) -> None:
            self._body = body

        def json(self) -> Any:
            return self._body

    class _Session:
        def __init__(self, *, impersonate: str | None = None) -> None:
            self.impersonate = impersonate
            calls["sessions"].append(self)

        def get(self, url: str, *, params: Any = None, headers: Any = None) -> _Response:
            calls["gets"].append({"url": url, "params": params, "headers": headers})
            if len(calls["gets"]) <= fail_times:
                raise _FakeRequestsError("push2his connection reset")
            return _Response(payload)

    requests_mod = types.ModuleType("curl_cffi.requests")
    requests_mod.Session = _Session  # type: ignore[attr-defined]
    requests_mod.RequestsError = _FakeRequestsError  # type: ignore[attr-defined]
    package = types.ModuleType("curl_cffi")
    package.requests = requests_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "curl_cffi", package)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", requests_mod)
    return calls


def test_kline_builds_the_request_contract(monkeypatch) -> None:
    calls = install_fake_curl_cffi(monkeypatch, payload={"data": {"klines": []}})
    eastmoney.kline("561010", beg="20240101")
    assert calls["sessions"][0].impersonate == "chrome"  # the configured browser fingerprint
    get = calls["gets"][0]
    assert get["url"] == "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    assert get["headers"]["Referer"] == "https://quote.eastmoney.com/"  # the header push2his needs
    params = get["params"]
    assert params["secid"] == "1.561010"  # 5x ETF → SSE market 1
    assert params["beg"] == "20240101"
    assert params["end"] == "20500101"
    assert params["klt"] == "101"  # daily
    assert params["fqt"] == "0"  # unadjusted
    assert params["ut"] == "7eea3edcaed734bea9cbfc24409ed989"
    assert params["fields1"] == "f1,f2,f3,f4,f5,f6"
    assert params["fields2"] == "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116"


def test_kline_secid_is_szse_for_1x_codes(monkeypatch) -> None:
    calls = install_fake_curl_cffi(monkeypatch, payload={"data": {"klines": []}})
    eastmoney.kline("159915", beg="20240101")
    assert calls["gets"][0]["params"]["secid"] == "0.159915"  # 1x ETF → SZSE market 0


def test_kline_parses_klines_into_domain_bars(monkeypatch) -> None:
    # data.klines rows are CSV: date,open,close,high,low,volume,amount,amplitude,pct,chg,turnover.
    # The domain bar keeps date, close (→ NAV), amount, and turnover; one call serves both legs.
    payload = {
        "data": {
            "klines": [
                "2026-06-15,1.10,1.12,1.13,1.09,1000,2222.0,1.5,0.9,0.01,0.42",
                "2026-06-16,1.12,1.15,1.16,1.11,1200,3333.0,1.8,2.7,0.03,0.55",
            ]
        }
    }
    install_fake_curl_cffi(monkeypatch, payload=payload)
    bars = eastmoney.kline("561010", beg="20240101")
    assert bars == [
        {"date": "2026-06-15", "close": 1.12, "amount": 2222.0, "turnover": 0.42},
        {"date": "2026-06-16", "close": 1.15, "amount": 3333.0, "turnover": 0.55},
    ]


def test_kline_empty_data_block_yields_no_bars(monkeypatch) -> None:
    # A delisted/unknown code returns an empty ``data`` block; degrade to [] so the caller's
    # multi-source reconciliation falls back to the other legs rather than crashing.
    install_fake_curl_cffi(monkeypatch, payload={"data": []})
    assert eastmoney.kline("561010", beg="20240101") == []


def test_kline_retries_once_then_recovers(monkeypatch) -> None:
    monkeypatch.setattr(eastmoney.time, "sleep", lambda _seconds: None)  # don't wait on the backoff
    calls = install_fake_curl_cffi(
        monkeypatch, payload={"data": {"klines": ["2026-06-16,1.1,1.2,1.3,1.0,9,5.0,1,2,3,0.4"]}},
        fail_times=1,
    )
    bars = eastmoney.kline("561010", beg="20240101")
    assert bars[0]["close"] == 1.2  # the second attempt succeeded
    assert len(calls["gets"]) == 2  # one retry after the first reset
    assert len(calls["sessions"]) == 2  # a fresh impersonating session per attempt


def test_kline_raises_after_the_single_retry(monkeypatch) -> None:
    monkeypatch.setattr(eastmoney.time, "sleep", lambda _seconds: None)
    calls = install_fake_curl_cffi(monkeypatch, fail_times=2)  # both attempts reset
    with pytest.raises(OSError):  # curl_cffi's RequestsError propagates to the resilience boundary
        eastmoney.kline("561010", beg="20240101")
    assert len(calls["gets"]) == 2  # exactly two attempts, then raise — no unbounded retry loop
