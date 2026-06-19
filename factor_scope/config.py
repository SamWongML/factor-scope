"""Run configuration and source selection.

Kept deliberately small; it can grow to carry store paths, the graph backend,
and the LLM provider selection. The key knobs are ``source`` (fixtures vs live) and the as-of
date, which together make a run deterministic and point-in-time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from factor_scope.cost import Price

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

# Reasoning is tiered so cost follows difficulty: the cheap DeepSeek V4 tiers do the
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


def _default_model_prices() -> dict[str, Price]:
    """USD per 1M in/out tokens — the table the cost meter prices token-only providers from.

    DeepSeek V4 list prices (verified 2026-06-08). ``claude_code`` reports its own USD
    in the ``--output-format json`` envelope, so the deep-think model needs no line; add one per
    priced model.
    """

    return {
        "deepseek-v4-flash": Price(input_per_mtok=0.14, output_per_mtok=0.28),
        "deepseek-v4-pro": Price(input_per_mtok=0.44, output_per_mtok=0.87),
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
    # None → the default tracks the source: a fixtures run takes the committed manifest stamp
    # (deterministic, byte-for-byte); a live run takes the run date (today), since the live store is
    # a moving append-only log whose point-in-time ceiling must advance. An explicit date wins.
    as_of: str | None = None
    output_path: Path = field(default=Path("out") / "dashboard.json")
    # Where the per-night dashboard history accumulates — one immutable ``<as_of>.json`` per run
    # plus an ``index.json`` manifest (see factor_scope.history). None → a ``dashboards/``
    # directory next to ``output_path``.
    history_dir: Path | None = None
    # Where the point-in-time store lives. None → an ephemeral in-memory store that `run`
    # auto-populates from the source, so the entrypoint works standalone. A path → a durable
    # append-only store that `ingest` fills and `run` reads (point-in-time).
    store_path: Path | None = None
    # Where the pre-materialized per-fund time-series gold lands — one compact ``<code>.json`` trail
    # per fund, appended one point per night (see factor_scope.series), so a chart serves flat with
    # no query in the request path. None → a ``series/`` directory next to ``output_path``.
    series_dir: Path | None = None
    # Where the nightly job publishes a read-only file replica of the store after each run, so an
    # ad-hoc query reads the replica — never the writer's handle — satisfying DuckDB's one-RW-or-
    # many-RO rule structurally (see factor_scope.store.replica). None → no replica is published.
    replica_path: Path | None = None
    # Where the durable connection graph lives. None → an ephemeral in-memory graph built from the
    # readings store at run time (mirrors store_path). A path → a durable, append-only graph.
    graph_path: Path | None = None
    # The medallion cold tier. None → the whole silver log stays hot in the DuckDB file. A path →
    # readings older than the hot window are tiered to Hive-partitioned Parquet (series=…/year=…)
    # there after each ingest, and every read unions hot + cold transparently — bounding the hot
    # file as history accrues (see factor_scope.store).
    cold_dir: Path | None = None
    # The hot-window retention: readings dated more than this many days before the run are tiered to
    # the cold dir. Only consulted when ``cold_dir`` is set; sized to comfortably exceed any ingest
    # re-fetch lookback so the append-time dedup (which only sees the hot table) stays correct.
    hot_window_days: int = 365
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
    # A per-night ceiling on how many items actually argue the bull/bear seats — a cost safety valve
    # for theme-rich nights. None (default) is unlimited. Items are debated in priority order
    # (holdings → watchlist → emerging); the overflow degrades to abstain-with-error in the run log,
    # the ceiling living outside the model exactly like the trend gate.
    max_debate_items: int | None = None
    # Where the nightly job appends its append-only ops run log (one JSON record per run).
    log_path: Path = field(default=Path("out") / "nightly.jsonl")
    # A monthly USD ceiling across *all* model spend — the nightly seats, the re-rank, and the
    # research job all meter into one append-only ledger (``spend_path``), and once the calendar
    # month's running total crosses this the remaining fresh work degrades to abstain-with-error /
    # fewer themes, leaving a partial-but-valid artifact. None (default) is unlimited; the cap lives
    # outside the model exactly like the trend gate. The deterministic fake meters nothing, so the
    # offline suite is never throttled.
    monthly_budget_usd: float | None = None
    # The append-only spend ledger every job books its per-(provider, model) cost into; the monthly
    # guard reads its month-to-date from here.
    spend_path: Path = field(default=Path("out") / "spend.jsonl")
    # USD-per-1M-token price table for the token-only providers (DeepSeek/Qwen/…); claude_code
    # reports its own USD. Add a line per model you charge (see ``_default_model_prices``).
    model_prices: dict[str, Price] = field(default_factory=_default_model_prices)
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
