"""Emerging radar funnel — industry → top-3 funds.

A two-stage funnel that turns a faint industry signal into a defensible shortlist: **Stage A**
qualifies the *industry* (signal strength, durability, lead-chain corroboration, an investable
wrapper); only a cleared theme reaches **Stage B**, which screens its candidate funds on a fixed
scorecard (methodology, overlap-with-core via the look-through, cost, liquidity, tracking,
concentration) and ranks them to a top 3. The digest then argues bull/bear over that
shortlist and promotes at most one. Deterministic and fixtures-first; reuses the graph for
overlap (no new graph logic).
"""

from __future__ import annotations

from factor_scope.emerging.funnel import Shortlist, group_by_theme, run_funnel
from factor_scope.emerging.mapping import ThemeFundLink, infer_links, return_correlation
from factor_scope.emerging.stage_a import (
    StageAResult,
    Theme,
    qualify_theme,
    signal_strength,
)
from factor_scope.emerging.stage_b import (
    Candidate,
    FundScore,
    overlap_with_core,
    score_fund,
    screen_funds,
)

__all__ = [
    "Candidate",
    "FundScore",
    "Shortlist",
    "StageAResult",
    "Theme",
    "ThemeFundLink",
    "group_by_theme",
    "infer_links",
    "overlap_with_core",
    "qualify_theme",
    "return_correlation",
    "run_funnel",
    "score_fund",
    "screen_funds",
    "signal_strength",
]
