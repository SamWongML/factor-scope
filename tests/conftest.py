"""Force the suite into offline mode.

Online-by-default: live sources + the real ``claude_code`` provider are the normal path, so the
defaults that ``Config`` and the CLI resolve are *live*. The test suite is the explicit offline
mode — fixtures + the deterministic ``fake`` provider — and stays hermetic + byte-for-byte
reproducible by selecting it here, before any test module (and thus ``factor_scope.config`` /
``factor_scope.cli``) is imported. Individual tests toggle ``FACTOR_SCOPE_OFFLINE`` via
``monkeypatch`` to pin the online defaults.
"""

from __future__ import annotations

import os

import pytest

os.environ["FACTOR_SCOPE_OFFLINE"] = "1"


@pytest.fixture(autouse=True)
def _no_keychain_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the offline suite hermetic: never shell out to the macOS Keychain.

    ``credentials.resolve_credential`` falls back to the login Keychain when an env var is unset; on
    a developer's Mac that could resolve a real secret and make the "missing key" negative tests
    pass spuriously (and break determinism by touching the host). Stub the lookup to ``None`` so an
    unset key reads as truly absent. The live canary opts back into real resolution by running under
    ``FACTOR_SCOPE_LIVE=1`` (``tests/integration/test_adapters_live.py``).
    """

    if os.environ.get("FACTOR_SCOPE_LIVE") != "1":
        monkeypatch.setattr("factor_scope.credentials._from_keychain", lambda _name: None)


@pytest.fixture(autouse=True)
def _reset_host_breaker() -> None:
    """The EastMoney host breaker is a run-scoped singleton; reset it before each test.

    A test that drives an adapter into an EastMoney refusal records a failure on the shared breaker;
    without this reset those failures would accumulate across tests and spuriously trip it open,
    making a later test skip the (faked) host it expected to try. The production reset is at the
    top of each market gather (``AShareMarket.gather``).
    """

    from factor_scope.ingest.base import host_breaker

    host_breaker.reset()


@pytest.fixture(autouse=True)
def _no_live_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the live inter-call pace in the offline suite so it stays fast + deterministic.

    The live wiring tests construct a ``LiveFeed`` with the production pacing default; left real it
    would sleep a jittered fraction before every per-fund call, slowing the suite and making timing
    nondeterministic. The real pacer runs against the network under ``FACTOR_SCOPE_LIVE=1``; here it
    is a no-op (a test that asserts pacing re-patches it to a recorder).
    """

    if os.environ.get("FACTOR_SCOPE_LIVE") != "1":
        monkeypatch.setattr("factor_scope.ingest.feed.pace_between_calls", lambda _seconds: None)
