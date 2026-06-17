"""Shared, network-free test doubles for the lazily-imported ``akshare`` module.

The ingest adapters import ``akshare`` inside their ``fetch_live`` bodies; these fakes let a unit
test inject a module exposing only the backend functions under test, with one DataFrame stand-in
that serves both the history adapters (``.iloc[-1]`` → the latest bar) and the iterating ones
(``.iterrows()`` → every row).
"""

from __future__ import annotations

import sys
import types
from typing import Any


class FakeFrame:
    """A pandas-free DataFrame stand-in over a list of row mappings."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    @property
    def iloc(self) -> _Iloc:
        return _Iloc(self._rows)

    def iterrows(self) -> Any:
        return enumerate(self._rows)


class _Iloc:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self._rows[index]


def install_fake_akshare(monkeypatch: Any, **funcs: Any) -> None:
    """Inject a network-free ``akshare`` module exposing only the given backend functions."""

    module = types.ModuleType("akshare")
    for name, fn in funcs.items():
        setattr(module, name, fn)
    monkeypatch.setitem(sys.modules, "akshare", module)
