"""The live credential preflight — fail fast on a missing required key before the expensive pull.

A missing credential is a permanent operator error (it never succeeds on retry), so it is caught
up front rather than discovered only after a multi-hour run leaves the macro/edgar dial degraded.
A transient feed outage is a different failure class, handled by the `_live_or_empty` boundary (see
``tests/integration/test_live_ingest_wiring.py``).

Credentials resolve env-then-Keychain; the autouse conftest fixture stubs the Keychain leg to
``None``, so an unset env var reads as genuinely absent here.
"""

import pytest

from factor_scope.config import Config
from factor_scope.markets.ashare import CredentialError, preflight_live_credentials

pytestmark = pytest.mark.unit


def test_preflight_fails_fast_when_fred_api_key_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(CredentialError, match="FRED_API_KEY"):
        preflight_live_credentials(Config(source="live"))


def test_preflight_passes_when_fred_key_is_set_and_no_edgar_ciks_configured(monkeypatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "x")
    monkeypatch.delenv("EDGAR_IDENTITY", raising=False)
    preflight_live_credentials(Config(source="live"))  # default edgar_ciks=() → no identity needed


def test_preflight_requires_edgar_identity_only_when_edgar_ciks_configured(monkeypatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "x")
    monkeypatch.delenv("EDGAR_IDENTITY", raising=False)
    preflight_live_credentials(Config(source="live"))  # no CIKs → EDGAR identity not required
    with pytest.raises(CredentialError, match="EDGAR_IDENTITY"):
        preflight_live_credentials(Config(source="live", edgar_ciks=("0001067983",)))
