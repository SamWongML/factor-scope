"""System gate for Phase 3 — the run artifact carries the look-through connections (spec §05).

End-to-end over the bundled fixtures: a name held by two of my funds (中际旭创, in both the optical-
module and comms ETFs) surfaces in those items' ``connections[]`` with ``connections_flag`` set, the
right ``also_in``, and my total look-through weight. Because the optical ETF reads reversal-DOWN
risk, the shared name is flagged falling (``↓``). Deterministic, schema-valid. Stays green at every
later phase boundary.
"""

import json

import pytest
from typer.testing import CliRunner

from factor_scope.cli import app
from factor_scope.config import Config
from factor_scope.contract import Dashboard
from factor_scope.pipeline import build_dashboard

pytestmark = pytest.mark.system

runner = CliRunner()


def test_run_attaches_lookthrough_connections(tmp_path) -> None:
    out = tmp_path / "dashboard.json"
    result = runner.invoke(app, ["run", "--output", str(out)])
    assert result.exit_code == 0, result.output

    dash = Dashboard.model_validate(json.loads(out.read_text(encoding="utf-8")))
    flagged = [it for it in dash.items if it.connections_flag]
    assert flagged, "no item surfaced a connection"

    # The optical-module ETF and the comms ETF both hold 中际旭创 → an illusion-of-diversification
    # overlap on both items.
    optical = next(it for it in dash.items if it.item.startswith("光通信"))
    assert optical.connections_flag is True
    shared = next(c for c in optical.connections if "中际旭创" in c.shared)
    assert "↓" in shared.shared  # the optical ETF reads reversal-DOWN risk → name flagged falling
    assert any("通信ETF" in name for name in shared.also_in)
    # My total look-through weight: 0.094*w(561010) + 0.052*w(515880) ≈ 0.0841 by market value.
    assert shared.lookthrough_wt == pytest.approx(0.0841, abs=1e-3)

    comms = next(it for it in dash.items if it.item == "通信ETF")
    assert comms.connections_flag is True
    assert any("中际旭创" in c.shared for c in comms.connections)


def test_connections_run_is_deterministic() -> None:
    cfg = Config()
    first = build_dashboard(cfg).model_dump_json(indent=2)
    second = build_dashboard(cfg).model_dump_json(indent=2)
    assert first == second
