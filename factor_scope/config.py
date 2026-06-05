"""Run configuration and source selection.

Kept deliberately small for Phase 0; later phases extend it with store paths, the graph backend,
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
    provider: str = "fake"  # LLM provider: "fake" (default) | "claude_code" | "deepseek"
