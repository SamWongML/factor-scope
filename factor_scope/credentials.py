"""Operator credential resolution — the environment first, then the macOS Keychain.

The live source keys (``FRED_API_KEY``, ``EDGAR_IDENTITY``) must resolve **identically** for an
interactive ``make live-check`` and the non-interactive launchd nightly. launchd sources no shell rc
(not ``.zshrc``, not even ``.zshenv``), so a key exported in a dotfile reaches the terminal but
never the scheduled job. The Keychain — read at runtime — is the single store both contexts share,
keeping secrets out of plaintext dotfiles and the launchd plist entirely.

Resolution is environment-first so an explicit ``KEY=…`` (a one-off override, or a test's
``monkeypatch.setenv``) still wins; the Keychain is the fallback when the variable is unset. Store a
key once with::

    security add-generic-password -A -s factor-scope -a FRED_API_KEY -w '<key>'

(`-A` lets the local ``security`` read it without an ACL prompt — needed for the non-interactive
nightly on a dedicated single-user host).
"""

from __future__ import annotations

import os
import subprocess
import sys

# The Keychain service every factor-scope generic-password item is filed under; the account is the
# credential's env-var name (FRED_API_KEY / EDGAR_IDENTITY), so one service holds the whole set.
KEYCHAIN_SERVICE = "factor-scope"

# Absolute path so launchd's minimal PATH still finds it; `security` always lives here on macOS.
_SECURITY = "/usr/bin/security"


def resolve_credential(name: str) -> str | None:
    """The value of ``name`` from the environment, else the macOS login Keychain, else ``None``.

    Environment-first: an exported override (or a test stub) wins over the stored secret. Returns
    ``None`` when neither holds it — callers (the live preflight, the FRED/EDGAR adapters) treat
    that as the credential being absent.
    """

    return os.environ.get(name) or _from_keychain(name)


def _from_keychain(name: str) -> str | None:
    """Read one generic password from the macOS login Keychain, or ``None`` if unavailable.

    macOS-only (returns ``None`` off Darwin, so CI and the offline suite never shell out). Any
    failure — no such item, a locked Keychain, ``security`` missing, a timeout — degrades to
    ``None`` rather than raising, so a missing key surfaces as the preflight's clear error, not a
    stack trace.
    """

    if sys.platform != "darwin":
        return None
    try:
        completed = subprocess.run(
            [_SECURITY, "find-generic-password", "-w", "-s", KEYCHAIN_SERVICE, "-a", name],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    # `security -w` prints the secret followed by a newline; an empty item is treated as absent.
    return completed.stdout.rstrip("\n") or None
