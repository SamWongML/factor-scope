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
import logging
import random
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, NamedTuple, Protocol, cast, runtime_checkable

from factor_scope.config import Config
from factor_scope.ingest import (
    _live_or_empty,
    baostock,
    eastmoney,
    etf_scale,
    fund_holdings,
    fund_universe,
    fundamentals,
    mootdx,
    prices,
    trading_activity,
)
from factor_scope.ingest.base import (
    EASTMONEY_KLINE,
    host_breaker,
    mark_provisional,
    run_date,
    settled_watermarks,
)
from factor_scope.store import PointInTimeStore, Reading

logger = logging.getLogger(__name__)

_SCORECARD = ("fee", "tracking_error", "top10_weight")


class _EmReadings(NamedTuple):
    """One EastMoney fetch split into its two legs — the NAV bar(s) and the activity bar(s).

    One ``push2his`` K-line call (or one shared spot row) feeds both the price leg (``nav``) and the
    crowding/illiquidity surface (``activity``), so a deep-pull fund costs one request, not two.
    """

    nav: list[Reading]
    activity: list[Reading]


class _ShapeDecision(NamedTuple):
    """One series' load decision for a code: the ``since`` floor, and whether to deep-pull.

    ``since`` is the watermark bars must clear: ``None`` (re)seeds the full window, a date keeps
    strictly past it. ``deep`` is whether a per-code K-line pull is needed; when False the spot
    board serves the current bar, floored at ``since`` so an already-held session isn't rewritten.
    """

    since: str | None
    deep: bool


def _sessions_between(latest: date, closed: date) -> int:
    """Trading sessions from ``latest`` (exclusive) to ``closed`` (inclusive) — the gap measure.

    A calendar-free proxy: weekdays (Mon–Fri) in ``(latest, closed]``. Steady state is one session
    behind (today's bar not yet recorded), so a normal weekend (Fri stored, Mon run) counts one, not
    three. Exchange holidays it can't see make it slightly conservative — an extra deep pull that
    merely re-confirms via the K-line, never a missed bar. ``closed <= latest`` (already current, or
    clock skew) is zero.
    """

    if closed <= latest:
        return 0
    days = (closed - latest).days
    return sum(1 for n in range(1, days + 1) if (latest + timedelta(days=n)).weekday() < 5)


def _settled_through(readings: list[Reading], through: str | None) -> list[Reading]:
    """Tag any deep-leg bar dated past ``through`` (the board's last settled session) provisional.

    The K-line window is settled history save for a current, still-forming bar an off-nominal
    intraday pull can include — the bar past the board's last settled session. Tagging it
    provisional (the spot leg's gate, shared) keeps the settled watermark on the real close, so a
    later pull backfills it rather than starting past a non-final bar. ``through`` None (code absent
    from the board) keeps every bar settled — no session to gate on. Bars are oldest-first, so the
    settled prefix precedes the provisional tail and order is preserved.
    """

    if through is None:
        return readings
    settled = [r for r in readings if r.as_of <= through]
    return settled + mark_provisional([r for r in readings if r.as_of > through])


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

    def log_backfill_deferral(self) -> None: ...


class LiveFeed:
    """The online edge — the network adapters, each lazily importing its heavy dependency.

    Every method delegates to the adapter's ``fetch_live`` backend, so the live transport (and its
    retry/timeout/failover resilience) stays in one place and is exercised against real sources by
    ``tests/integration/test_adapters_live.py`` under ``FACTOR_SCOPE_LIVE=1``. Each *per-fund* call
    is preceded by a jittered pace (``pace_seconds``) so the full-universe loop's hundreds of
    sequential hits to the one rate-limited EastMoney host stay under its IP limiter.

    One feed is constructed per run and is store-aware: it carries the point-in-time ``store`` and
    owns the per-code spot-vs-deep load-shape decision. Steady state reads each fund's current bar
    off the shared spot board (one batch call, no per-code ``push2his`` hit); a cold or gapped fund
    takes one K-line pull that seeds/backfills **both** the NAV and trading-activity legs.
    """

    def __init__(
        self,
        store: PointInTimeStore | None,
        pace_seconds: float = 0.0,
        *,
        impersonate: str = "chrome",
        gap_sessions: int = 2,
        cap: int = 80,
    ) -> None:
        self._store = store
        self._pace_seconds = pace_seconds
        self._impersonate = impersonate
        self._gap_sessions = gap_sessions
        self._cap = cap
        self._budget = cap  # the per-run deep-pull budget, spent greedily as cold/gap codes arrive
        self._deferred = 0  # cold/gap codes routed to the board because the budget was exhausted
        self._spot: dict[str, Any] | None = None
        self._em_cache: dict[str, _EmReadings] = {}
        self._watermark_cache: dict[str, dict[str, str]] = {}
        self._seeded_cache: dict[str, set[str]] = {}
        self._run_stamp: str | None = None

    @property
    def _spot_board(self) -> dict[str, Any]:
        """The whole-market spot board, pulled once and shared across the legs that read it.

        Lazy so a prices-only use never pulls it; memoised so the universe, scale, and current-bar
        legs of one run share a single snapshot rather than re-fetching per leg.
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
        # The activity leg of the unified EastMoney fetch. Live, ``since`` is ignored — the feed
        # reads the store directly for the load-shape decision — but it stays on the protocol for
        # the cassette feed. The universe loop wraps this in the resilience boundary, so the K-line
        # pull (on the first access for a deep code) is retry/timeout/failover-bounded.
        pace_between_calls(self._pace_seconds)
        return self._em(code, fetched_at=fetched_at).activity

    def valuation(self, code: str, *, fetched_at: str, since: str | None = None) -> list[Reading]:
        pace_between_calls(self._pace_seconds)
        return fundamentals.fetch_live(code, fetched_at=fetched_at, since=since)

    def price_sources(
        self, code: str, *, fetched_at: str, since: str | None = None
    ) -> list[list[Reading]]:
        # Three corroborating legs reconciled per date. The EastMoney leg shares the unified
        # per-code fetch with the activity leg (memoised, so one ``push2his`` call feeds both);
        # Baostock and Mootdx hit their own un-throttled hosts on the ``since`` watermark. Each runs
        # behind the resilience boundary (retry + wall-clock deadline + logged failover), so a
        # blocked or hung scraper contributes an empty read and the surviving legs reconcile, rather
        # than killing the run. The EastMoney leg already handles its own breaker/Sina fallback
        # inside ``_em``, so its boundary here only bounds a hang — it never adds a spurious retry.
        pace_between_calls(self._pace_seconds)
        return [
            _live_or_empty(self._em_nav, code, source=prices.SOURCE, fetched_at=fetched_at),
            _live_or_empty(
                baostock.fetch_live,
                code,
                source=baostock.SOURCE,
                fetched_at=fetched_at,
                since=since,
            ),
            _live_or_empty(
                mootdx.fetch_live, code, source=mootdx.SOURCE, fetched_at=fetched_at, since=since
            ),
        ]

    def _em_nav(self, code: str, *, fetched_at: str) -> list[Reading]:
        """The NAV leg of the unified EastMoney fetch — the resilience-wrapped price-leg entry."""

        return self._em(code, fetched_at=fetched_at).nav

    def _pin_run(self, fetched_at: str) -> None:
        """Bind the feed to one run's stamp — the single-run lifetime its memo caches assume.

        The per-code K-line memo, the per-series settled-watermark map, and the per-series seeded
        set are keyed on code/series alone; that is correct only because one feed serves exactly one
        run at one stamp (``as_of`` and the seed floor both derive from ``fetched_at``). Reusing a
        feed across stamps would silently return the first run's reads, so a mismatch is a loud
        failure, not a stale hit — the snapshot boundary, enforced rather than merely assumed.
        """

        if self._run_stamp is None:
            self._run_stamp = fetched_at
        elif self._run_stamp != fetched_at:
            raise RuntimeError(
                f"LiveFeed is single-run: bound to {self._run_stamp}, reused with {fetched_at}"
            )

    def _em(self, code: str, *, fetched_at: str) -> _EmReadings:
        """One EastMoney fetch per code, feeding BOTH the NAV and trading-activity legs.

        The store-aware load-shape: a code whose stored history is **cold** (no settled history, or
        a span that doesn't reach back the seed window) or **gapped** (more than ``gap_sessions``
        sessions behind the closed session) takes one ``push2his`` K-line pull that seeds/backfills
        both legs; every other code reads its current bar off the shared spot board — so steady
        state makes ~zero per-code history calls. The deep pulls are **capped per run**
        (:meth:`_grant_deep_pull`): codes arrive in tier priority and the budget is spent greedily,
        so once it is exhausted the remaining cold/gap codes fall to the board too — bounding the
        per-run history burst even on a cold start. Memoised per code so the universe (activity) and
        price loops share the single fetch.
        """

        self._pin_run(fetched_at)  # the memo below is keyed on code alone — bind the run's stamp
        if code in self._em_cache:
            return self._em_cache[code]
        closed = run_date(fetched_at)
        as_of = closed.isoformat() if closed is not None else fetched_at[:10]
        seed_floor = prices._floor(fetched_at, None)  # the cold (re)seed floor
        price = self._load_shape(prices.SERIES, code, as_of, closed, seed_floor)
        activity = self._load_shape(trading_activity.SERIES, code, as_of, closed, seed_floor)
        # The K-line ``beg`` and the client-side floor both derive from the same per-leg ``since``,
        # so the window requested is the window kept — a re-seed backfills its early bars instead of
        # clipping them at the watermark. ``since`` None floors at the seed window, else past it.
        price_floor = price.since if price.since is not None else seed_floor
        activity_floor = activity.since if activity.since is not None else seed_floor
        # A deep pull runs when the host is reachable and the per-run budget grants it. When the
        # K-line breaker is already open no push2his pull is possible, so spend no budget on a call
        # that can't be made — the code still enters ``_deep`` for its Sina/spot fallback. The cap
        # therefore bounds real pulls only, and its deferral log stays a signal about the cap, not
        # the host outage (which :func:`ingest._check_eastmoney_health` reports separately).
        if (price.deep or activity.deep) and (
            host_breaker.is_open(EASTMONEY_KLINE) or self._grant_deep_pull()
        ):
            beg = min(
                prices._em_start(fetched_at, shape.since)
                for shape in (price, activity)
                if shape.deep
            )
            result = self._deep(
                code,
                beg=beg,
                fetched_at=fetched_at,
                nav_floor=price_floor,
                act_floor=activity_floor,
            )
        else:
            # Warm (the board serves the current session), or a cold/gap code deferred because the
            # per-run deep-pull budget is spent — either way the current bar comes off the board,
            # floored per leg, and (when deferred) its seeding resumes a later night.
            settled = self._settled(code, closed)
            result = _EmReadings(
                nav=prices.spot_reading(
                    self._spot_board,
                    code,
                    fetched_at=fetched_at,
                    settled=settled,
                    floor=price_floor,
                ),
                activity=trading_activity.spot_reading(
                    self._spot_board,
                    code,
                    fetched_at=fetched_at,
                    settled=settled,
                    floor=activity_floor,
                ),
            )
        self._em_cache[code] = result
        return result

    def _grant_deep_pull(self) -> bool:
        """Spend one unit of the per-run deep-pull budget, or defer when it is exhausted.

        Called once a code's load-shape wants a deep pull and the K-line host is reachable (an open
        breaker can make no pull, so it spends no budget). The cap bounds per-run ``push2his``
        history calls — the defense-in-depth guarantee that holds even if impersonation fails: codes
        arrive in tier priority (book/core before probation), so the budget seeds the most important
        funds first. A code that wants a deep pull after the budget is spent is **deferred** — it
        falls to the fresh spot bar and its K-line seeding resumes a later night — and counted for
        the run-end deferral report (:meth:`log_backfill_deferral`).
        """

        if self._budget > 0:
            self._budget -= 1
            return True
        self._deferred += 1
        return False

    def log_backfill_deferral(self) -> None:
        """Surface one run-level line when the deep-pull cap deferred cold-start/gap seeding.

        Called once at run end (by the market gather). When the per-run cap bound, the cold-start or
        gap codes streamed past the budget fell to the fresh spot bar and their K-line seeding is
        deferred to a later night; this reports that count so the operator can see cold start and
        outage recovery progressing across nights — nothing is silently dropped. Silent when nothing
        was deferred (the steady-state common case), so the line stays a real signal, not noise.
        """

        if self._deferred:
            logger.warning(
                "ingest: EastMoney deep-pull cap (%d) reached — %d cold-start/gap fund(s) deferred "
                "to the spot board this run; their K-line seeding resumes a later night (the "
                "universe converges over multiple runs, nothing dropped)",
                self._cap,
                self._deferred,
            )

    def _deep(
        self, code: str, *, beg: str, fetched_at: str, nav_floor: str | None, act_floor: str | None
    ) -> _EmReadings:
        """The deep path: one impersonating K-line pull mapped into both legs; fall back on refusal.

        On a ``push2his`` refusal (or the run-scoped breaker already open) the NAV leg backs onto
        Sina's settled daily close while the activity leg falls to the current spot bar tagged
        provisional, so a block on the history host degrades the surfaces to today's bar rather than
        dropping the fund. The breaker spans both legs (one shared host), and the run-level alarm
        (:func:`ingest._check_eastmoney_health`) reads its failure count unchanged.
        """

        if not host_breaker.is_open(EASTMONEY_KLINE):
            try:
                bars = eastmoney.kline(code, beg=beg, impersonate=self._impersonate)
                host_breaker.record_success(EASTMONEY_KLINE)
                nav = prices.from_kline(code, bars, fetched_at=fetched_at, floor=nav_floor)
                activity = trading_activity.from_kline(
                    code, bars, fetched_at=fetched_at, floor=act_floor
                )
                # The current, still-forming bar (past the board's last settled session) is tagged
                # provisional like the spot leg. The cutoff is read off the board only once a bar
                # has landed, so an empty pull stays board-free (and network-free in isolation).
                through = self._board_date(code) if nav or activity else None
                return _EmReadings(
                    nav=_settled_through(nav, through),
                    activity=_settled_through(activity, through),
                )
            except Exception as exc:
                host_breaker.record_failure(EASTMONEY_KLINE)
                logger.warning(
                    "ingest: EastMoney K-line refused %s (%s); NAV → Sina, activity → spot board",
                    code,
                    exc,
                )
        return _EmReadings(
            nav=self._sina(code, fetched_at=fetched_at, floor=nav_floor),
            activity=trading_activity.spot_reading(
                self._spot_board, code, fetched_at=fetched_at, settled=False, floor=act_floor
            ),
        )

    def _sina(self, code: str, *, fetched_at: str, floor: str | None) -> list[Reading]:
        """Sina NAV fallback, swallowed to no rows on its own failure so ``_em`` never raises."""

        try:
            return prices.sina(code, fetched_at=fetched_at, floor=floor)
        except Exception:
            logger.warning(
                "ingest: Sina NAV fallback failed for %s; no NAV this run", code, exc_info=True
            )
            return []

    def _load_shape(
        self, series: str, code: str, as_of: str, closed: date | None, seed_floor: str | None
    ) -> _ShapeDecision:
        """This series' load :class:`_ShapeDecision` for ``code`` — its ``since`` floor + deep flag.

        Cold (no store, no real run date, no settled history, or a span that doesn't reach the seed
        window) (re)seeds from the ~650-day floor; a gap (> ``gap_sessions`` behind the *board's*
        last settled session) pulls incrementally from the watermark; else the spot board serves
        the current session. The ceiling is the board's session date, not the wall clock, so an
        exchange holiday — over which the board does not advance — keeps a warm fund on the cheap
        board rather than firing a per-fund K-line that merely re-confirms no new session exists.
        """

        watermark = self._watermarks(series, as_of).get(code)
        if self._store is None or closed is None or seed_floor is None or watermark is None:
            return _ShapeDecision(None, True)  # cold: seed the full window
        if code not in self._seeded(series, seed_floor):
            return _ShapeDecision(None, True)  # span shorter than the seed window → re-seed
        board = self._board_date(code)
        ceiling = date.fromisoformat(board) if board is not None else closed
        if _sessions_between(date.fromisoformat(watermark), ceiling) > self._gap_sessions:
            return _ShapeDecision(watermark, True)  # gap → incremental from the watermark
        return _ShapeDecision(watermark, False)  # warm: spot board serves the current bar

    def _board_date(self, code: str) -> str | None:
        """The board's session date (ISO) for ``code`` — the latest *settled* close it attests.

        The spot board advances its session date only once a session has closed, so its date is the
        last settled session: both the gap measure's holiday-aware ceiling (it does not move during
        a closure) and the deep leg's settle cutoff (a K-line bar past it is the current, unsettled
        session). ``None`` when the code is absent from the board, or its row carries no date — no
        session reference to read.
        """

        row = self._spot_board.get(code)
        return row.get("date") if row is not None else None

    def _settled(self, code: str, closed: date | None) -> bool:
        """Is the spot bar a settled session — its session ``date`` the expected closed session?

        True at the post-close (22:00) schedule on a trading day, so the cheap board bar records as
        settled history and advances the watermark. An intraday/holiday/weekend run whose board date
        differs stays provisional, so a later K-line pull backfills rather than mis-settling a bar.
        """

        if closed is None:
            return False
        return self._board_date(code) == closed.isoformat()

    def _watermarks(self, series: str, as_of: str) -> dict[str, str]:
        """The settled watermark per code in ``series`` (:func:`ingest.base.settled_watermarks`).

        Memoised: one store read per series, shared across the run's load-shape decisions.
        """

        if series not in self._watermark_cache:
            self._watermark_cache[series] = settled_watermarks(self._store, series, as_of)
        return self._watermark_cache[series]

    def _seeded(self, series: str, seed_floor: str) -> set[str]:
        """Codes whose stored history reaches back to ``seed_floor`` — the seed window is filled.

        A code with a settled bar at or before ``run_date − seed window`` has enough history for the
        trend gate's 200-day MA to rank against a stable own-history distribution; one that doesn't
        is treated as cold and re-seeded. Memoised: one store read per series per run.
        """

        if series not in self._seeded_cache:
            if self._store is None:
                self._seeded_cache[series] = set()
            else:
                rows = self._store.read_as_of(series, seed_floor, excluding="provisional")
                self._seeded_cache[series] = {r.key for r in rows}
        return self._seeded_cache[series]


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

    def log_backfill_deferral(self) -> None:
        """No-op offline: the cassette replays recorded bars with no cap or deep-pull deferral."""


def get_feed(config: Config, store: PointInTimeStore | None) -> Feed:
    """The online network adapters by default; the committed recordings in the offline test mode.

    The live feed is store-aware (it carries ``store`` for the per-code load-shape decision); the
    offline cassette feed ignores it and replays the recordings deterministically.
    """

    if config.source == "live":
        return LiveFeed(
            store,
            pace_seconds=config.live_pacing_seconds,
            impersonate=config.eastmoney_impersonate,
            gap_sessions=config.eastmoney_gap_sessions,
            cap=config.eastmoney_deep_pull_cap,
        )
    return CassetteFeed(config.fixtures_dir / "cassettes")
