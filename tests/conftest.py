"""Force the suite into offline mode (U05).

Online-by-default: live sources + the real ``claude_code`` provider are the normal path, so the
defaults that ``Config`` and the CLI resolve are *live*. The test suite is the explicit offline
mode — fixtures + the deterministic ``fake`` provider — and stays hermetic + byte-for-byte
reproducible by selecting it here, before any test module (and thus ``factor_scope.config`` /
``factor_scope.cli``) is imported. Individual tests toggle ``FACTOR_SCOPE_OFFLINE`` via
``monkeypatch`` to pin the online defaults.
"""

from __future__ import annotations

import os

os.environ["FACTOR_SCOPE_OFFLINE"] = "1"
