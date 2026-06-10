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


# Discovery's LLM is split into two difficulty tiers so cost follows the work (see assess.py):
# the cheap ``DRAFT`` model digests the bulky raw materials, the strong ``JUDGE`` model renders the
# durability/lead-chain verdict. Roles are the registry keys; each tier is swapped on its own.
DISCOVERY_DRAFT = "draft"
DISCOVERY_JUDGE = "judge"

# Reasoning is tiered so cost follows difficulty (ROADMAP §8–§9): the cheap DeepSeek V4 tiers do the
# bulk and mid-tier work, and the expensive deep-think seat is reserved for the one hard call — the
# final bull/bear→synthesis debate. Explicit model IDs only (the ``deepseek-chat`` /
# ``deepseek-reasoner`` aliases deprecate 2026-07-24).
TIER_FLASH = "flash"  # deepseek-v4-flash: bulk extraction/summarization/coarse scoring
TIER_PRO = "pro"  # deepseek-v4-pro: mid-tier structured ranking
TIER_DEEP_THINK = "deep_think"  # the seats' tier — Claude Opus-class via the headless `claude` CLI

# The reasoning tasks and the tier each routes to (cheap-first; the debate is the reserved call).
TASK_DEBATE = "debate"  # the final bull/bear→synthesis seats
TASK_BULK = "bulk"  # extraction / summarization / coarse scoring


def _default_reasoning_tiers() -> dict[str, str]:
    """Tier → explicit model id. Flash/Pro are bare V4 ids; deep-think is a `claude` CLI alias."""

    return {
        TIER_FLASH: "deepseek-v4-flash",
        TIER_PRO: "deepseek-v4-pro",
        TIER_DEEP_THINK: "opus",
    }


def _default_task_tiers() -> dict[str, str]:
    """Default cheap-first routes — the debate alone earns the deep-think seat."""

    return {TASK_DEBATE: TIER_DEEP_THINK, TASK_BULK: TIER_FLASH}


@dataclass(frozen=True)
class ModelSpec:
    """One swappable model definition.

    ``model`` is a provider-prefixed id (``deepseek:…`` / ``openai:…`` / ``anthropic:…`` /
    ``moonshotai:…``) that Pydantic-AI resolves directly. To reach any *other* OpenAI-compatible
    endpoint — Qwen / GLM / Kimi on a self-hosted or vendor gateway — set ``base_url`` (and the env
    var holding its key in ``api_key_env``). Switching a tier's model is editing this one line: no
    per-model code, no fallback chain.
    """

    model: str
    base_url: str | None = None
    api_key_env: str | None = None


def _default_discovery_models() -> dict[str, ModelSpec]:
    """The default two tiers — DeepSeek V4 flash (draft) + pro (judge), each swapped alone."""

    return {
        DISCOVERY_DRAFT: ModelSpec("deepseek:deepseek-v4-flash"),
        DISCOVERY_JUDGE: ModelSpec("deepseek:deepseek-v4-pro"),
    }


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
    # Theme-discovery (the separate, user/cron-triggered service) knobs. Its LLM judgment is
    # stratified by task difficulty to cut cost — a cheap draft tier digests the raw materials, a
    # strong judge tier renders the verdict (see ``ModelSpec`` / assess.py). Each tier is swapped
    # independently; the heavy bull/bear debate stays on ``claude_code`` (``provider``). The offline
    # test mode selects the deterministic fakes via ``source``.
    discovery_models: dict[str, ModelSpec] = field(default_factory=_default_discovery_models)
    # The rolling text corpus discovery clusters. Live pulls this feed; the offline test mode reads
    # the bundled ``textstream.csv`` instead (same ``doc_id,as_of,source,text`` shape).
    textstream_feed_url: str | None = None
    # The multilingual sentence-embedding model online BERTopic uses — local and free (auto-selects
    # the Mac mini's MPS or CPU). The light default suits a Chinese A-share corpus; swap to a
    # heavier one like BAAI/bge-m3 by editing this line.
    discovery_embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    # Clusters the online MiniBatchKMeans carves the corpus into — BERTopic needs an explicit k (the
    # online clusterer is not density-based). Raise it for a broader stream.
    discovery_n_topics: int = 12
    # The reasoning tiers (tier → model id) and the task → tier routing. Cost follows difficulty:
    # the final bull/bear→synthesis debate runs on the reserved deep-think seat, bulk jobs run
    # cheap-first on DeepSeek V4 Flash. Each tier swaps on its own line (see ``model_for_task``).
    reasoning_tiers: dict[str, str] = field(default_factory=_default_reasoning_tiers)
    task_tiers: dict[str, str] = field(default_factory=_default_task_tiers)

    def tier_for_task(self, task: str) -> str:
        """Which difficulty tier a reasoning task routes to."""

        return self.task_tiers[task]

    def model_for_task(self, task: str) -> str:
        """The concrete model id a reasoning task resolves to, through its tier."""

        return self.reasoning_tiers[self.task_tiers[task]]
