"""Run configuration and source selection.

Kept deliberately small; it can grow to carry store paths, the graph backend,
and the LLM provider selection. The key knobs are ``source`` (fixtures vs live) and the as-of
date, which together make a run deterministic and point-in-time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Repo-root-relative default location of the committed sample data.
DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"


@dataclass(frozen=True)
class Config:
    """Everything a single run needs to be reproducible."""

    source: str = "fixtures"  # "fixtures" (default, offline) | "live" (opt-in)
    fixtures_dir: Path = field(default=DEFAULT_FIXTURES_DIR)
    as_of: str | None = None  # None → take the as-of stamped in the fixtures (deterministic)
    output_path: Path = field(default=Path("out") / "dashboard.json")
    # Where the point-in-time store lives. None → an ephemeral in-memory store that `run`
    # auto-populates from the source, so the entrypoint works standalone. A path → a durable
    # append-only store that `ingest` fills and `run` reads (point-in-time).
    store_path: Path | None = None
    # Where the durable connection graph lives. None → an ephemeral in-memory graph built from the
    # readings store at run time (mirrors store_path). A path → a durable, append-only graph.
    graph_path: Path | None = None
    # US fund/ETF CIKs whose monthly N-PORT holdings the ``--live`` path pulls from EDGAR to feed
    # the look-through graph. Empty by default — the fixtures path never reads it.
    edgar_ciks: tuple[str, ...] = ()
    # Relative band within which the two CN price sources (AkShare/Baostock) corroborate. Defaults
    # to the SEC/CSSF NAV-error materiality baseline (0.5%); raise it per equity ETFs, lower it for
    # money-market funds. A same-day gap beyond it flags the reading (never kills the run).
    corroboration_tolerance: float = 0.005
    # Digestion judgment provider: "fake" (default, offline) | "claude_code". DeepSeek is a chore
    # model (off the judgment path), not a judgment provider — see digest.get_provider.
    provider: str = "fake"
    # Where the nightly job appends its append-only ops run log (one JSON record per run).
    log_path: Path = field(default=Path("out") / "nightly.jsonl")
