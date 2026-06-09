"""Run configuration and source selection.

Kept deliberately small; it can grow to carry store paths, the graph backend,
and the LLM provider selection. The key knobs are ``source`` (fixtures vs live) and the as-of
date, which together make a run deterministic and point-in-time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Repo-root-relative default location of the committed sample data.
DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"

# Online-by-default: live sources + the real provider are the normal path. Offline — fixtures
# + the deterministic ``fake`` provider — is the explicit test/ops mode, opted into by this env var
# (the suite sets it; see tests/conftest.py) or the CLI ``--offline`` flag. Determinism is preserved
# by the snapshot boundary + mocks, not by avoiding the network.
OFFLINE_ENV = "FACTOR_SCOPE_OFFLINE"


def offline_mode() -> bool:
    """True when offline mode is on — a truthy ``FACTOR_SCOPE_OFFLINE`` (``""``/``"0"`` are off)."""

    return os.environ.get(OFFLINE_ENV, "") not in ("", "0")


@dataclass(frozen=True)
class Config:
    """Everything a single run needs to be reproducible."""

    # "live" (default — online-by-default) | "fixtures" (the offline test/ops mode). Defaults track
    # ``offline_mode()`` so a bare ``Config()`` honours the env toggle the suite sets.
    source: str = field(default_factory=lambda: "fixtures" if offline_mode() else "live")
    # Which market's adapters supply the readings — selected by name (see markets.get_market). The
    # only concrete adapter is "ashare"; this is the seam later markets target. Orthogonal to
    # ``source``: the market owns *which* sources, ``source`` owns fixtures-vs-live for each.
    market: str = "ashare"
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
    # US fund/ETF CIKs whose monthly N-PORT holdings the live path pulls from EDGAR to feed
    # the look-through graph. Empty by default — the fixtures path never reads it.
    edgar_ciks: tuple[str, ...] = ()
    # Relative band within which the two CN price sources (AkShare/Baostock) corroborate. Defaults
    # to the SEC/CSSF NAV-error materiality baseline (0.5%); raise it per equity ETFs, lower it for
    # money-market funds. A same-day gap beyond it flags the reading (never kills the run).
    corroboration_tolerance: float = 0.005
    # Digestion judgment provider: "claude_code" (default — online) | "fake" (the offline stub).
    # Tracks ``offline_mode()`` like ``source``. DeepSeek is a chore model (off the judgment path),
    # not a judgment provider — see digest.get_provider.
    provider: str = field(default_factory=lambda: "fake" if offline_mode() else "claude_code")
    # Where the nightly job appends its append-only ops run log (one JSON record per run).
    log_path: Path = field(default=Path("out") / "nightly.jsonl")
    # Theme-discovery (the separate, user/cron-triggered service) knobs. The LLM that populates the
    # durability/corroboration fields is the *lighter* job → DeepSeek V4 by default. A built-in
    # provider prefix (``deepseek:`` / ``openai:`` / ``anthropic:`` / ``moonshotai:``) passes
    # straight through; set ``discovery_base_url`` (+ api-key env) to point at *any*
    # OpenAI-compatible endpoint — Qwen / GLM / Kimi — with no code change. The heavy bull/bear
    # debate stays on ``claude_code`` (``provider``). Offline selects the fakes via ``source``.
    discovery_model: str = "deepseek:deepseek-v4-pro"
    discovery_base_url: str | None = None
    discovery_api_key_env: str | None = None
    # The rolling text corpus the live discovery clusters. Offline reads the bundled
    # ``textstream.csv``; online pulls this feed (same ``doc_id,as_of,source,text`` shape).
    textstream_feed_url: str | None = None
    # The multilingual sentence-embedding model BERTopic-online uses; the light, MPS-friendly
    # default suits a Mac-mini-scale Chinese corpus (swap to a heavier one like BAAI/bge-m3 later).
    discovery_embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
