"""The single, stable CLI entrypoint (`factor-scope`).

`run` is the load-bearing command: it builds the morning artifact and prints it. Its contract
(emit a schema-valid ``dashboard.json`` + a terminal render) always holds; deeper analysis
only enriches what fills the artifact, never the shape of the contract.
"""

from __future__ import annotations

from pathlib import Path

import typer

from factor_scope.config import (
    DEFAULT_FIXTURES_DIR,
    DISCOVERY_DRAFT,
    DISCOVERY_JUDGE,
    Config,
    ModelSpec,
    offline_mode,
)
from factor_scope.pipeline import discover as discover_pipeline
from factor_scope.pipeline import ingest as ingest_pipeline
from factor_scope.pipeline import nightly as nightly_pipeline
from factor_scope.pipeline import run as run_pipeline
from factor_scope.render import render

app = typer.Typer(
    add_completion=False,
    help="factor-scope — nightly decision-support artifact for an A-share/funds book.",
    no_args_is_help=True,
)


@app.command()
def run(
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Offline test mode: bundled fixtures + the deterministic `fake` provider "
        "(default is live sources + the real provider).",
    ),
    fixtures_dir: Path = typer.Option(
        DEFAULT_FIXTURES_DIR, help="Directory holding the committed sample data."
    ),
    as_of: str | None = typer.Option(
        None, help="Override the as-of date (YYYY-MM-DD). Defaults to the fixture's stamp."
    ),
    output: Path = typer.Option(
        Path("out") / "dashboard.json", "--output", "-o", help="Where to write dashboard.json."
    ),
    history_dir: Path | None = typer.Option(
        None,
        "--history-dir",
        help="Where the per-night history accumulates (default: dashboards/ next to the artifact).",
    ),
    store_path: Path | None = typer.Option(
        None,
        "--store-path",
        help="Read from a durable store (else an in-memory one auto-ingested from the source).",
    ),
    cold_dir: Path | None = typer.Option(
        None,
        "--cold-dir",
        help="The store's cold tier, if one was configured at ingest; reads union hot + cold. "
        "Omitting it against a tiered store silently drops the tiered history.",
    ),
    graph_path: Path | None = typer.Option(
        None,
        "--graph-path",
        help="Read from a durable connection graph (else one built in-memory from the store).",
    ),
    provider: str = typer.Option(
        "claude_code",
        help="Digestion judgment provider: claude_code (default) | fake (offline stub). "
        "(DeepSeek is a chore model, off the judgment path.)",
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Write the artifact without printing."),
) -> None:
    """Build the morning artifact and print it."""

    is_offline = offline or offline_mode()
    config = Config(
        source="fixtures" if is_offline else "live",
        fixtures_dir=fixtures_dir,
        as_of=as_of,
        output_path=output,
        history_dir=history_dir,
        store_path=store_path,
        cold_dir=cold_dir,
        graph_path=graph_path,
        provider="fake" if is_offline else provider,
    )
    dash = run_pipeline(config)
    if not quiet:
        typer.echo(render(dash))
    typer.echo(f"\n✓ wrote {output}", err=True)


@app.command()
def ingest(
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Offline test mode: ingest bundled fixtures (default is live sources).",
    ),
    fixtures_dir: Path = typer.Option(
        DEFAULT_FIXTURES_DIR, help="Directory holding the committed sample data."
    ),
    as_of: str | None = typer.Option(
        None, help="The as-of date to stamp positions with (YYYY-MM-DD). Defaults to the manifest."
    ),
    store_path: Path = typer.Option(
        Path("out") / "store.duckdb", "--store-path", help="The durable store to append into."
    ),
    graph_path: Path = typer.Option(
        Path("out") / "graph.ladybug",
        "--graph-path",
        help="The durable connection graph to materialise from the holdings feeds.",
    ),
    cold_dir: Path | None = typer.Option(
        None,
        "--cold-dir",
        help="Tier readings older than the hot window into Hive-partitioned Parquet here "
        "(series=…/year=…); reads union hot + cold. Unset = keep the whole log hot.",
    ),
    hot_window_days: int = typer.Option(
        365,
        "--hot-window-days",
        help="How many days of readings stay hot in the DuckDB file before tiering to --cold-dir.",
    ),
) -> None:
    """Fill the point-in-time store + connection graph from a source, so `run` can read them."""

    config = Config(
        source="fixtures" if (offline or offline_mode()) else "live",
        fixtures_dir=fixtures_dir,
        as_of=as_of,
        store_path=store_path,
        graph_path=graph_path,
        cold_dir=cold_dir,
        hot_window_days=hot_window_days,
    )
    n = ingest_pipeline(config)
    typer.echo(f"✓ appended {n} readings to {store_path}; built graph at {graph_path}", err=True)


@app.command()
def discover(
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Offline test mode: the bundled text corpus + deterministic fakes "
        "(default is the live feed + BERTopic + the configured LLM).",
    ),
    fixtures_dir: Path = typer.Option(
        DEFAULT_FIXTURES_DIR, help="Directory holding the committed sample data."
    ),
    as_of: str | None = typer.Option(
        None, help="The as-of date to stamp discovered themes with. Defaults to the manifest."
    ),
    store_path: Path = typer.Option(
        Path("out") / "store.duckdb",
        "--store-path",
        help="The durable store to append the discovered themes (+ corpus) into.",
    ),
    judge_model: str = typer.Option(
        "deepseek:deepseek-v4-pro",
        "--judge-model",
        help="The strong tier — the durability/lead-chain verdict. Provider-prefixed "
        "(deepseek/openai/anthropic/moonshotai); --base-url reaches any OpenAI-compatible API.",
    ),
    draft_model: str = typer.Option(
        "deepseek:deepseek-v4-flash",
        "--draft-model",
        help="The cheap tier — digests the raw materials before the judge. Stratified by "
        "difficulty to cut cost; same provider-prefix / --base-url switching as --judge-model.",
    ),
    embedding_model: str = typer.Option(
        "paraphrase-multilingual-MiniLM-L12-v2",
        "--embedding-model",
        help="The local sentence-embedding model online BERTopic uses (free; MPS/CPU on a Mac).",
    ),
    base_url: str | None = typer.Option(
        None, "--base-url", help="OpenAI-compatible endpoint applied to both tiers (Qwen/GLM/Kimi)."
    ),
    api_key_env: str | None = typer.Option(
        None, "--api-key-env", help="Env var holding the API key for --base-url."
    ),
    feed_url: str | None = typer.Option(
        None, "--feed-url", help="Live text-corpus feed (doc_id,as_of,source,text CSV)."
    ),
    spend_path: Path = typer.Option(
        Path("out") / "spend.jsonl",
        "--spend-path",
        help="The append-only cross-job spend ledger the monthly budget reads (JSONL); share it "
        "with the nightly's so the ceiling spans both.",
    ),
    monthly_budget: float | None = typer.Option(
        None,
        "--monthly-budget",
        help="A monthly USD ceiling across all model spend; over it, discovery stops assessing "
        "further themes (the ones done are written). Unset = unlimited.",
    ),
) -> None:
    """Discover candidate themes from the rolling text stream and append them to the store.

    A separate, user/cron-triggered research service — not the nightly. It writes ``themes``
    Readings (with cited evidence) that the next ``ingest`` maps to funds and ``run`` surfaces in
    the emerging list. Schedule it weekly with ``factor-scope schedule --job discover``.
    """

    is_offline = offline or offline_mode()
    config = Config(
        source="fixtures" if is_offline else "live",
        fixtures_dir=fixtures_dir,
        as_of=as_of,
        store_path=store_path,
        discovery_models={
            DISCOVERY_DRAFT: ModelSpec(draft_model, base_url=base_url, api_key_env=api_key_env),
            DISCOVERY_JUDGE: ModelSpec(judge_model, base_url=base_url, api_key_env=api_key_env),
        },
        discovery_embedding_model=embedding_model,
        textstream_feed_url=feed_url,
        spend_path=spend_path,
        monthly_budget_usd=monthly_budget,
    )
    n = discover_pipeline(config)
    typer.echo(f"✓ discovered {n} themes into {store_path}", err=True)


@app.command()
def nightly(
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Offline test mode: bundled fixtures + the deterministic `fake` provider "
        "(default is live sources + the real provider).",
    ),
    fixtures_dir: Path = typer.Option(
        DEFAULT_FIXTURES_DIR, help="Directory holding the committed sample data."
    ),
    as_of: str | None = typer.Option(
        None, help="Override the as-of date (YYYY-MM-DD). Defaults to the fixture's stamp."
    ),
    output: Path = typer.Option(
        Path("out") / "dashboard.json", "--output", "-o", help="Where to write dashboard.json."
    ),
    history_dir: Path | None = typer.Option(
        None,
        "--history-dir",
        help="Where the per-night history accumulates (default: dashboards/ next to the artifact).",
    ),
    store_path: Path = typer.Option(
        Path("out") / "store.duckdb",
        "--store-path",
        help="The durable store leans persist into (so tomorrow's self-scoring can read them).",
    ),
    graph_path: Path = typer.Option(
        Path("out") / "graph.ladybug", "--graph-path", help="The durable connection graph."
    ),
    cold_dir: Path | None = typer.Option(
        None,
        "--cold-dir",
        help="Tier readings older than the hot window into Hive-partitioned Parquet here "
        "(series=…/year=…); reads union hot + cold. Unset = keep the whole log hot.",
    ),
    hot_window_days: int = typer.Option(
        365,
        "--hot-window-days",
        help="How many days of readings stay hot in the DuckDB file before tiering to --cold-dir.",
    ),
    log_path: Path = typer.Option(
        Path("out") / "nightly.jsonl", "--log-path", help="The append-only ops run log (JSONL)."
    ),
    spend_path: Path = typer.Option(
        Path("out") / "spend.jsonl",
        "--spend-path",
        help="The append-only cross-job spend ledger the monthly budget reads (JSONL).",
    ),
    monthly_budget: float | None = typer.Option(
        None,
        "--monthly-budget",
        help="A monthly USD ceiling across all model spend; over it, the run throttles to a "
        "partial-but-valid artifact. Unset = unlimited.",
    ),
    provider: str = typer.Option(
        "claude_code", help="Digestion judgment provider: claude_code (default) | fake (offline)."
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Run without printing the artifact."),
) -> None:
    """Run the one-shot nightly job: ingest → compute → digest → artifact, run log, persisted calls.

    This is the production entrypoint: unlike ``run``, it defaults to a *durable* store
    so each night's leans accumulate as falsifiable calls, and it appends a structured ops record
    to the run log. Schedule it with ``factor-scope schedule`` (launchd on macOS, cron on Linux).
    """

    is_offline = offline or offline_mode()
    config = Config(
        source="fixtures" if is_offline else "live",
        fixtures_dir=fixtures_dir,
        as_of=as_of,
        output_path=output,
        history_dir=history_dir,
        store_path=store_path,
        graph_path=graph_path,
        cold_dir=cold_dir,
        hot_window_days=hot_window_days,
        log_path=log_path,
        spend_path=spend_path,
        monthly_budget_usd=monthly_budget,
        provider="fake" if is_offline else provider,
    )
    dash, record = nightly_pipeline(config)
    if not quiet:
        typer.echo(render(dash))
    typer.echo(
        f"\n✓ nightly {record.as_of}: {record.n_items} items "
        f"({record.n_abstain} abstained), {record.n_calls_logged} calls logged · "
        f"{record.provider} · snapshot {record.snapshot_id[:12]} · "
        f"wrote {output}, logged to {log_path}",
        err=True,
    )


@app.command()
def serve(
    history_dir: Path = typer.Option(
        Path("out") / "dashboards",
        "--history-dir",
        help="The per-night dashboard history to serve (what `run`/`nightly` record).",
    ),
    host: str = typer.Option("127.0.0.1", help="Interface to bind — localhost by default."),
    port: int = typer.Option(8765, help="Port to bind."),
) -> None:
    """Serve the dashboard history as a read-only JSON API (needs the ``serve`` extra).

    The frontend seam over the recorded nights: ``/dashboards`` lists them,
    ``/dashboards/{as_of}`` reopens one, ``/dashboards/latest`` is the newest, and
    ``/openapi.json`` is the typed schema to generate a client from. Read-only by
    construction — it never ingests or reasons.
    """

    import uvicorn  # lazy: the pinned serve extra is only needed when actually serving

    from factor_scope.serve import create_app

    uvicorn.run(create_app(history_dir), host=host, port=port)


@app.command()
def schedule(
    kind: str = typer.Option(
        "launchd", help="launchd (macOS, the Mac-mini production path) | cron (Linux)."
    ),
    job: str = typer.Option(
        "nightly",
        help="Which job to schedule: nightly (the run) | discover (the weekly theme service).",
    ),
    label: str = typer.Option(
        "com.factor-scope.nightly", help="launchd job label (reverse-DNS)."
    ),
    hour: int = typer.Option(22, help="Local hour to fire the nightly job (24h)."),
    minute: int = typer.Option(0, help="Minute past the hour to fire."),
    working_dir: Path = typer.Option(
        Path("."), help="Working directory for the job (resolved to an absolute path)."
    ),
    stdout_path: Path = typer.Option(
        Path("out") / "nightly.out.log", help="Where the job's stdout is appended."
    ),
    stderr_path: Path = typer.Option(
        Path("out") / "nightly.err.log", help="Where the job's stderr is appended."
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write the rendered config here instead of stdout."
    ),
) -> None:
    """Emit the scheduler config for the nightly job — a launchd plist (default) or a cron line.

    Pure render: install the plist under ``~/Library/LaunchAgents`` and
    ``launchctl load`` it, or add the cron line to your crontab. See ``docs/ops/RUNBOOK.md``.
    """

    from factor_scope.schedule import (
        DEFAULT_PROGRAM,
        DISCOVER_PROGRAM,
        ScheduleSpec,
        render_cron_line,
        render_launchd_plist,
    )

    if job == "nightly":
        program = DEFAULT_PROGRAM
    elif job == "discover":
        program = DISCOVER_PROGRAM
    else:
        raise typer.BadParameter(f"unknown --job {job!r}; use 'nightly' or 'discover'")

    spec = ScheduleSpec(
        label=label,
        program_arguments=program,
        hour=hour,
        minute=minute,
        working_directory=working_dir.resolve(),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    if kind == "cron":
        text = render_cron_line(spec)
    elif kind == "launchd":
        text = render_launchd_plist(spec)
    else:
        raise typer.BadParameter(f"unknown --kind {kind!r}; use 'launchd' or 'cron'")

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        typer.echo(f"✓ wrote {output}", err=True)
    else:
        typer.echo(text)


@app.command()
def schema(
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write the JSON schema here instead of stdout."
    ),
) -> None:
    """Print (or write) the dashboard.json JSON schema."""

    import json

    from factor_scope.contract import dashboard_json_schema

    text = json.dumps(dashboard_json_schema(), indent=2, ensure_ascii=False)
    if output:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"✓ wrote {output}", err=True)
    else:
        typer.echo(text)


if __name__ == "__main__":
    app()
