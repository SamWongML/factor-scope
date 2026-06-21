"""Credential resolution — environment first, then the macOS Keychain.

The same resolver serves the interactive ``make live-check`` and the non-interactive launchd
nightly, so a key stored once in the Keychain reaches both without any plaintext in a dotfile or the
plist. The offline suite is kept off the real Keychain by an autouse conftest stub; the test that
exercises the Keychain shell-out captures the *real* function at import time to bypass that stub.
"""

import subprocess

import pytest

from factor_scope import credentials

pytestmark = pytest.mark.unit

# Captured before any fixture runs (collection time), so it is the real implementation even though
# the autouse conftest fixture replaces ``credentials._from_keychain`` for every test.
_REAL_FROM_KEYCHAIN = credentials._from_keychain


def test_resolve_prefers_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "from-env")
    # even if the Keychain held a value, the explicit env override wins
    monkeypatch.setattr(credentials, "_from_keychain", lambda _name: "from-keychain")
    assert credentials.resolve_credential("FRED_API_KEY") == "from-env"


def test_resolve_falls_back_to_the_keychain_when_env_unset(monkeypatch) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(credentials, "_from_keychain", lambda name: f"kc:{name}")
    assert credentials.resolve_credential("FRED_API_KEY") == "kc:FRED_API_KEY"


def test_resolve_returns_none_when_neither_holds_it(monkeypatch) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    # the autouse conftest fixture already stubs the Keychain to None — an absent key reads as None
    assert credentials.resolve_credential("FRED_API_KEY") is None


def test_keychain_returns_none_off_macos(monkeypatch) -> None:
    monkeypatch.setattr(credentials.sys, "platform", "linux")
    # no shell-out at all off Darwin
    monkeypatch.setattr(
        credentials.subprocess, "run", lambda *a, **k: pytest.fail("must not run security")
    )
    assert _REAL_FROM_KEYCHAIN("FRED_API_KEY") is None


def test_keychain_reads_the_secret_on_success(monkeypatch) -> None:
    monkeypatch.setattr(credentials.sys, "platform", "darwin")
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="s3cret\n", stderr="")

    monkeypatch.setattr(credentials.subprocess, "run", fake_run)
    assert _REAL_FROM_KEYCHAIN("FRED_API_KEY") == "s3cret"  # trailing newline stripped
    # queried the factor-scope service with the var name as the account
    assert credentials.KEYCHAIN_SERVICE in seen["cmd"] and "FRED_API_KEY" in seen["cmd"]


def test_keychain_returns_none_when_item_missing(monkeypatch) -> None:
    monkeypatch.setattr(credentials.sys, "platform", "darwin")
    # `security` exits non-zero when the item is absent
    monkeypatch.setattr(
        credentials.subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 44, stdout="", stderr="not found"),
    )
    assert _REAL_FROM_KEYCHAIN("FRED_API_KEY") is None


def test_keychain_degrades_to_none_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(credentials.sys, "platform", "darwin")

    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 5.0)

    monkeypatch.setattr(credentials.subprocess, "run", boom)
    assert _REAL_FROM_KEYCHAIN("FRED_API_KEY") is None  # a timeout/locked keychain never raises
