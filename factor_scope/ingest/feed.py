"""The ingest transport seam — the only live-vs-offline difference, at the very edge.

A :class:`Feed` yields the raw market reads the A-share universe loop and the multi-source price
reconciliation run over. :class:`LiveFeed` pulls them from the network adapters (each lazily
imports its heavy dependency inside its ``fetch_live``); :class:`CassetteFeed` replays committed
recordings under ``data/fixtures/cassettes/`` so the **same** ingest code — the universe loop, the
incremental watermark, the multi-source reconciliation, delisting detection, and the content dedup
— runs offline and shells out to no network.

Determinism is preserved: recorded responses plus the deterministic ``fetched_at_for(as_of)`` keep
``dashboard.json`` byte-for-byte, and the snapshot boundary still freezes the reasoning input.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from factor_scope.config import Config
from factor_scope.ingest import (
    _live_or_empty,
    baostock,
    etf_scale,
    fund_holdings,
    fund_universe,
    fundamentals,
    mootdx,
    prices,
    trading_activity,
)
from factor_scope.store import PointInTimeStore, Reading

_SCORECARD = ("fee", "tracking_error", "top10_weight")


def pace_between_calls(seconds: float) -> None:
    """Pause a jittered ``[0, seconds]`` between sequential live per-fund calls (live path only).

    The full-universe loop hits one rate-limited EastMoney host hundreds of times in a row; a small
    randomised pace keeps the request rate under the IP limiter that otherwise drops the connection,
    without lengthening the nightly run materially. Full jitter (not a fixed delay) avoids a
    metronomic request cadence. ``seconds <= 0`` disables it. Only :class:`LiveFeed` calls this;
    the offline cassette replay never sleeps, so the suite stays fast and deterministic.
    """

    if seconds > 0:
        time.sleep(random.uniform(0.0, seconds))


@runtime_checkable
class Feed(Protocol):
    """The market edge: the raw reads the universe loop and price reconciliation consume.

    A read returns the rows knowable for that source as-of the run; the universe loop applies the
    incremental ``since`` watermark and the per-fund resilience boundary, and the price source is
    reconciled across the three legs — all in the ingest code, identically for live and offline.
    """

    def universe(self, *, as_of: str, fetched_at: str) -> list[Reading]: ...

    def etf_scale(self, *, fetched_at: str) -> list[Reading]: ...

    def holdings(
        self, fund: str, *, fetched_at: str, since: str | None = None
    ) -> list[Reading]: ...

    def activity(
        self, code: str, *, fetched_at: str, since: str | None = None
    ) -> list[Reading]: ...

    def valuation(
        self, code: str, *, fetched_at: str, since: str | None = None
    ) -> list[Reading]: ...

    def price_sources(
        self, code: str, *, fetched_at: str, since: str | None = None
    ) -> list[list[Reading]]: ...


class LiveFeed:
    """The online edge — the network adapters, each lazily importing its heavy dependency.

    Every method delegates to the adapter's ``fetch_live`` backend, so the live transport (and its
    retry/timeout/failover resilience) stays in one place and is exercised against real sources by
    ``tests/integration/test_adapters_live.py`` under ``FACTOR_SCOPE_LIVE=1``. Each *per-fund* call
    is preceded by a jittered pace (``pace_seconds``) so the full-universe loop's hundreds of
    sequential hits to the one rate-limited EastMoney host stay under its IP limiter.

    One feed is constructed per run and is store-aware: it carries the point-in-time ``store`` for
    the per-code spot-vs-deep load-shape decision.
    """

    def __init__(
        self,
        store: PointInTimeStore | None,
        pace_seconds: float = 0.0,
        *,
        impersonate: str = "chrome",
    ) -> None:
        self._store = store
        self._pace_seconds = pace_seconds
        self._impersonate = impersonate
        self._spot: dict[str, Any] | None = None

    @property
    def _spot_board(self) -> dict[str, Any]:
        """The whole-market spot board, pulled once and shared across the legs that read it.

        Lazy so a prices-only use never pulls it; memoised so the universe, scale, and
        activity-fallback legs of one run share a single snapshot rather than re-fetching per leg.
        """

        if self._spot is None:
            self._spot = etf_scale.fetch_spot_board()
        return self._spot

    def universe(self, *, as_of: str, fetched_at: str) -> list[Reading]:
        return fund_universe.fetch_live(self._spot_board, as_of=as_of, fetched_at=fetched_at)

    def etf_scale(self, *, fetched_at: str) -> list[Reading]:
        return etf_scale.fetch_live(self._spot_board, fetched_at=fetched_at)

    def holdings(self, fund: str, *, fetched_at: str, since: str | None = None) -> list[Reading]:
        pace_between_calls(self._pace_seconds)
        return fund_holdings.fetch_live(fund, fetched_at=fetched_at, since=since)

    def activity(self, code: str, *, fetched_at: str, since: str | None = None) -> list[Reading]:
        pace_between_calls(self._pace_seconds)
        return trading_activity.fetch_live(
            self._spot_board,
            code,
            fetched_at=fetched_at,
            since=since,
            impersonate=self._impersonate,
        )

    def valuation(self, code: str, *, fetched_at: str, since: str | None = None) -> list[Reading]:
        pace_between_calls(self._pace_seconds)
        return fundamentals.fetch_live(code, fetched_at=fetched_at, since=since)

    def price_sources(
        self, code: str, *, fetched_at: str, since: str | None = None
    ) -> list[list[Reading]]:
        # Each leg runs behind the resilience boundary (retry + wall-clock deadline + logged
        # failover), so a blocked or hung scraper contributes an empty read and the surviving legs
        # are reconciled, rather than killing the run. The ``since`` watermark threads through so a
        # nightly re-pull fetches only sessions past the store's floor (cold start seeds a window).
        pace_between_calls(self._pace_seconds)
        bao = baostock.fetch_live
        return [
            _live_or_empty(
                prices.fetch_live,
                code,
                source=prices.SOURCE,
                fetched_at=fetched_at,
                since=since,
                impersonate=self._impersonate,
            ),
            _live_or_empty(
                bao, code, source=baostock.SOURCE, fetched_at=fetched_at, since=since
            ),
            _live_or_empty(
                mootdx.fetch_live, code, source=mootdx.SOURCE, fetched_at=fetched_at, since=since
            ),
        ]


class CassetteFeed:
    """The offline edge — committed recordings replayed through the same ingest code.

    Each cassette is the recorded shape of one source's response, at realistic shape (the whole
    universe, multi-quarter holdings, multi-hundred-bar price/valuation/activity histories). The
    per-fund series honour the incremental ``since`` watermark exactly as the live adapters do, so a
    re-pull over an unchanged snapshot yields no newer rows.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[str, Any] = {}

    def _read(self, name: str) -> Any:
        if name not in self._cache:
            self._cache[name] = json.loads((self._root / name).read_text(encoding="utf-8"))
        return self._cache[name]

    def _rows(self, name: str) -> list[dict[str, Any]]:
        return cast("list[dict[str, Any]]", self._read(name))

    def _by_key(self, name: str, key: str) -> list[dict[str, Any]]:
        return cast("dict[str, list[dict[str, Any]]]", self._read(name)).get(key, [])

    def universe(self, *, as_of: str, fetched_at: str) -> list[Reading]:
        readings: list[Reading] = []
        for r in self._rows("universe.json"):
            scorecard = {k: r[k] for k in _SCORECARD}
            readings.append(
                Reading(
                    series=fund_universe.SERIES,
                    key=r["code"],
                    as_of=as_of,
                    fetched_at=fetched_at,
                    payload={
                        "name": r["name"],
                        "type": r["type"],
                        "on_exchange": r["on_exchange"],
                        "inception": r["inception"],
                        "delisting": r["delisting"],
                        **scorecard,
                        "valid": all(v is not None for v in scorecard.values()),
                    },
                )
            )
        return readings

    def etf_scale(self, *, fetched_at: str) -> list[Reading]:
        return [
            Reading(
                series=etf_scale.SERIES,
                key=r["code"],
                as_of=r["as_of"],
                fetched_at=fetched_at,
                payload={"exchange": r["exchange"], "aum": r["aum"], "shares": r["shares"]},
            )
            for r in self._rows("etf_scale.json")
        ]

    def holdings(self, fund: str, *, fetched_at: str, since: str | None = None) -> list[Reading]:
        return [
            Reading(
                series=fund_holdings.SERIES,
                key=f"{fund}/{r['holding']}",
                as_of=r["as_of"],
                fetched_at=fetched_at,
                payload={"fund": fund, "holding": r["holding"], "weight": r["weight"]},
            )
            for r in self._by_key("holdings.json", fund)
            if since is None or r["as_of"] > since
        ]

    def activity(self, code: str, *, fetched_at: str, since: str | None = None) -> list[Reading]:
        return [
            Reading(
                series=trading_activity.SERIES,
                key=code,
                as_of=r["as_of"],
                fetched_at=fetched_at,
                payload={"turnover": r["turnover"], "amount": r["amount"]},
            )
            for r in self._by_key("activity.json", code)
            if since is None or r["as_of"] > since
        ]

    def valuation(self, code: str, *, fetched_at: str, since: str | None = None) -> list[Reading]:
        return [
            Reading(
                series=fundamentals.SERIES,
                key=code,
                as_of=r["as_of"],
                fetched_at=fetched_at,
                payload={"pe": r["pe"]},
            )
            for r in self._by_key("valuation.json", code)
            if since is None or r["as_of"] > since
        ]

    def price_sources(
        self, code: str, *, fetched_at: str, since: str | None = None
    ) -> list[list[Reading]]:
        bars = self._by_key("prices.json", code)
        # One recorded NAV history, replayed as each of the three corroborating legs, so the
        # per-date reconciliation runs offline exactly as it does live (the legs agree → no flag).
        # The ``since`` watermark trims to sessions past the floor, honouring the same incremental
        # contract the live adapters do, so an offline re-pull is a no-op like the other series.
        return [
            [
                Reading(
                    series=prices.SERIES,
                    key=code,
                    as_of=r["as_of"],
                    fetched_at=fetched_at,
                    payload={"nav": r["nav"], "source": source},
                )
                for r in bars
                if since is None or r["as_of"] > since
            ]
            for source in (prices.SOURCE, baostock.SOURCE, mootdx.SOURCE)
        ]


def get_feed(config: Config, store: PointInTimeStore | None) -> Feed:
    """The online network adapters by default; the committed recordings in the offline test mode.

    The live feed is store-aware (it carries ``store`` for the per-code load-shape decision); the
    offline cassette feed ignores it and replays the recordings deterministically.
    """

    if config.source == "live":
        return LiveFeed(
            store, pace_seconds=config.live_pacing_seconds, impersonate=config.eastmoney_impersonate
        )
    return CassetteFeed(config.fixtures_dir / "cassettes")
