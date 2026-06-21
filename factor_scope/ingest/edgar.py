"""US lead-chain adapter (EDGAR 13F / N-PORT). `edgar.csv → {filer, as_of, holding, shares}`.

The US-side holdings that feed the cross-market lead and the connection graph. Each ``(filer,
holding)`` pair is its own point-in-time key. The fixture and live 13F carry ``shares`` (US lead);
live N-PORT instead carries a ``weight`` so US fund/ETF holdings become look-through ``HOLDS`` edges
(see ``fetch_live`` / ``build_graph_from_store``). Live is EdgarTools — never called in CI.
"""

from __future__ import annotations

from pathlib import Path

from factor_scope.credentials import resolve_credential
from factor_scope.ingest.base import as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "edgar"
FIXTURE = "edgar.csv"
_REQUIRED = ("filer", "as_of", "holding", "shares")


def parse(text: str, *, fetched_at: str) -> list[Reading]:
    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        filer = required_str(row, "filer", line_no, SERIES)
        as_of = required_str(row, "as_of", line_no, SERIES)
        holding = required_str(row, "holding", line_no, SERIES)
        shares = as_float(row, "shares", line_no, SERIES)
        readings.append(
            Reading(
                series=SERIES,
                key=f"{filer}/{holding}",
                as_of=as_of,
                fetched_at=fetched_at,
                payload={"filer": filer, "holding": holding, "shares": shares},
            )
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)


def fetch_live(
    cik: str, *, form: str = "13F-HR", fetched_at: str
) -> list[Reading]:  # pragma: no cover - live path
    """Pull a filer's latest holdings via EdgarTools. Requires the `live` extra + network.

    ``form="13F-HR"`` reads a 13F manager's quarterly share positions (the ``infotable``) into
    ``{filer, holding, shares}`` — the US lead-chain, not a graph edge. ``"NPORT-P"`` reads a
    fund/ETF's monthly portfolio (``investment_data``) keyed by security name into ``{filer,
    holding, weight}`` (``pct_value`` → fraction of net assets), so US fund/ETF holdings become
    look-through ``HOLDS`` edges alongside the CN funds.

    The SEC requires a User-Agent identity or it refuses the request; it is resolved
    env-then-Keychain (:func:`factor_scope.credentials.resolve_credential`) and set on EdgarTools
    here, so the launchd nightly — which never sees ``.zshrc`` — is self-sufficient rather than
    trusting an ambient var.
    """

    from edgar import Company, set_identity

    identity = resolve_credential("EDGAR_IDENTITY")
    if identity:
        set_identity(identity)
    filing = Company(cik).get_filings(form=form).latest(1)
    obj = filing.obj()
    as_of = str(filing.filing_date)
    if form == "13F-HR":
        return [
            Reading(
                series=SERIES,
                key=f"{cik}/{holding}",
                as_of=as_of,
                fetched_at=fetched_at,
                payload={"filer": cik, "holding": holding, "shares": shares},
            )
            for holding, shares in (
                (str(row["Ticker"]), float(row["SharesPrnAmount"]))
                for _, row in obj.infotable.iterrows()
            )
        ]
    return [  # NPORT-P — monthly fund/ETF portfolio, weighted for the look-through graph
        Reading(
            series=SERIES,
            key=f"{cik}/{holding}",
            as_of=as_of,
            fetched_at=fetched_at,
            payload={"filer": cik, "holding": holding, "weight": weight},
        )
        for holding, weight in (
            (str(row["name"]), float(row["pct_value"]) / 100.0)
            for _, row in obj.investment_data().iterrows()
        )
    ]
