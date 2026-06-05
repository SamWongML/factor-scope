"""The single, stable CLI entrypoint (`factor-scope`).

`run` is the load-bearing command: it builds the morning artifact and prints it. Its contract
(emit a schema-valid ``dashboard.json`` + a terminal render) holds at every phase boundary;
later phases only enrich what fills the artifact.
"""

from __future__ import annotations

from pathlib import Path

import typer

from factor_scope.config import DEFAULT_FIXTURES_DIR, Config
from factor_scope.pipeline import ingest as ingest_pipeline
from factor_scope.pipeline import run as run_pipeline
from factor_scope.render import render

app = typer.Typer(
    add_completion=False,
    help="Wealth-Assistant Engine — nightly decision-support artifact for an A-share/funds book.",
    no_args_is_help=True,
)


@app.command()
def run(
    fixtures: bool = typer.Option(
        True,
        "--fixtures/--live",
        help="Use bundled sample data (default) or opt in to live sources (Phase 1+).",
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
    store_path: Path | None = typer.Option(
        None,
        "--store-path",
        help="Read from a durable store (else an in-memory one auto-ingested from the source).",
    ),
    provider: str = typer.Option(
        "fake", help="LLM provider for digestion: fake (default) | claude_code | deepseek."
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Write the artifact without printing."),
) -> None:
    """Build the morning artifact and print it."""

    config = Config(
        source="fixtures" if fixtures else "live",
        fixtures_dir=fixtures_dir,
        as_of=as_of,
        output_path=output,
        store_path=store_path,
        provider=provider,
    )
    dash = run_pipeline(config)
    if not quiet:
        typer.echo(render(dash))
    typer.echo(f"\n✓ wrote {output}", err=True)


@app.command()
def ingest(
    fixtures: bool = typer.Option(
        True,
        "--fixtures/--live",
        help="Ingest bundled sample data (default) or opt in to live sources.",
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
) -> None:
    """Fill the point-in-time store from a source (append-only), so `run` can read it."""

    config = Config(
        source="fixtures" if fixtures else "live",
        fixtures_dir=fixtures_dir,
        as_of=as_of,
        store_path=store_path,
    )
    n = ingest_pipeline(config)
    typer.echo(f"✓ appended {n} readings to {store_path}", err=True)


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
