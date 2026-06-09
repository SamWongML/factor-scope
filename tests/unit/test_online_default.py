"""U05 — online-by-default.

Live sources + the real ``claude_code`` provider are the normal path; offline (fixtures + the
deterministic ``fake`` provider) is an explicit mode, selected by the ``FACTOR_SCOPE_OFFLINE`` env
var (or the CLI ``--offline`` flag). The suite forces offline (see ``tests/conftest.py``); these
tests toggle the env to pin both ends of the flip.
"""

from __future__ import annotations

import pytest

from factor_scope.config import Config, offline_mode

pytestmark = pytest.mark.unit

OFFLINE_ENV = "FACTOR_SCOPE_OFFLINE"


def test_config_is_online_by_default(monkeypatch) -> None:
    monkeypatch.delenv(OFFLINE_ENV, raising=False)
    assert offline_mode() is False
    cfg = Config()
    assert cfg.source == "live"
    assert cfg.provider == "claude_code"


def test_offline_env_selects_fixtures_and_the_fake_provider(monkeypatch) -> None:
    monkeypatch.setenv(OFFLINE_ENV, "1")
    assert offline_mode() is True
    cfg = Config()
    assert cfg.source == "fixtures"
    assert cfg.provider == "fake"


def test_offline_env_zero_is_online(monkeypatch) -> None:
    # An explicit "0"/"" reads as online — only a truthy value opts into the offline test mode.
    monkeypatch.setenv(OFFLINE_ENV, "0")
    assert offline_mode() is False
    assert Config().source == "live"
