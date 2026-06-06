"""Prices adapter (CN, Baostock) — a second, independent source for the ``prices`` series.

The spec's CN ingestion is dual/triple-sourced (AkShare + Baostock + Mootdx) so one scraper being
IP-blocked or offline never kills a nightly run (L1 / §04). This module is the Baostock leg: a thin
``fetch_live`` that reads the latest daily close for one ETF, stamped into the same ``prices``
:class:`~factor_scope.store.Reading` shape as the AkShare adapter. It therefore either corroborates
the AkShare read (cross-validation) or substitutes for it when AkShare is down — the selection
policy lives in :func:`factor_scope.ingest.prices.select_corroborated`.

Mootdx/pytdx is the planned third source and is tracked as a follow-up (see issue #21).

Like every live backend, the heavy dependency is imported lazily inside the call so the core
installs and CI run offline; ``fetch_live`` is opt-in (behind ``--live``) and never called in CI.
"""

from __future__ import annotations

from factor_scope.ingest.prices import SERIES
from factor_scope.store import Reading

SOURCE = "baostock"  # this adapter's provenance tag
_ADJUST_NONE = "3"  # Baostock adjustflag: 1=后复权 2=前复权 3=不复权 — raw close, matching AkShare


def _market_code(code: str) -> str:
    """Prefix a bare ETF code with its Baostock market — ``5x`` is Shanghai, ``1x`` is Shenzhen."""

    return f"sh.{code}" if code.startswith("5") else f"sz.{code}"


def fetch_live(code: str, *, fetched_at: str) -> list[Reading]:  # pragma: no cover - opt-in
    """Pull the latest daily close for one ETF via Baostock. Requires the `live` extra + network."""

    import baostock as bs

    bs.login()
    try:
        result = bs.query_history_k_data_plus(
            _market_code(code), "date,close", frequency="d", adjustflag=_ADJUST_NONE
        )
        rows: list[list[str]] = []
        while result.next():
            rows.append(result.get_row_data())
    finally:
        bs.logout()

    if not rows:  # delisted/unknown code or a query-level error → no data, so the caller falls back
        return []
    as_of, close = rows[-1][0], rows[-1][1]
    return [
        Reading(
            series=SERIES,
            key=code,
            as_of=str(as_of),
            fetched_at=fetched_at,
            payload={"nav": float(close), "source": SOURCE},
        )
    ]
