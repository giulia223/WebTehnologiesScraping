"""Command-line entry point: `techdetect <command>`.

The four commands mirror the pipeline: collect (network) -> detect (offline)
-> evaluate -> report. They are stubs until their tasks are implemented.
"""

from pathlib import Path
from typing import Annotated

import typer

from techdetect.config import Config

app = typer.Typer(
    help="Detect website technologies with evidence.",
    no_args_is_help=True,
)

# Every flag defaults to None, meaning "not passed": the Config default then applies.
InputOpt = Annotated[Path | None, typer.Option("--input", help="Domain list to crawl.")]
RawDirOpt = Annotated[Path | None, typer.Option("--raw-dir", help="Raw artifacts folder.")]
OutputDirOpt = Annotated[Path | None, typer.Option("--output-dir", help="Results folder.")]
ConcurrencyOpt = Annotated[
    int | None, typer.Option("--concurrency", min=1, help="Sites fetched in parallel.")
]
TimeoutOpt = Annotated[
    float | None, typer.Option("--timeout", min=0.1, help="Static fetch timeout in seconds.")
]
UserAgentOpt = Annotated[str | None, typer.Option("--user-agent", help="HTTP User-Agent.")]


def _not_implemented(command: str, config: Config) -> None:
    # Exit code 1, not 0: a script calling a stub must not believe it succeeded.
    typer.echo(f"{command}: not implemented yet")
    raise typer.Exit(code=1)


@app.command()
def collect(
    input_path: InputOpt = None,
    raw_dir: RawDirOpt = None,
    concurrency: ConcurrencyOpt = None,
    static_timeout_s: TimeoutOpt = None,
    user_agent: UserAgentOpt = None,
) -> None:
    """Fetch raw artifacts for each domain (network)."""
    config = Config().with_overrides(
        input_path=input_path,
        raw_dir=raw_dir,
        concurrency=concurrency,
        static_timeout_s=static_timeout_s,
        user_agent=user_agent,
    )
    _not_implemented("collect", config)


@app.command()
def detect(raw_dir: RawDirOpt = None, output_dir: OutputDirOpt = None) -> None:
    """Find technologies in saved raw artifacts (offline)."""
    config = Config().with_overrides(raw_dir=raw_dir, output_dir=output_dir)
    _not_implemented("detect", config)


@app.command()
def evaluate(output_dir: OutputDirOpt = None) -> None:
    """Measure precision and recall against hand labels."""
    config = Config().with_overrides(output_dir=output_dir)
    _not_implemented("evaluate", config)


@app.command()
def report(output_dir: OutputDirOpt = None) -> None:
    """Write the final tables and summaries."""
    config = Config().with_overrides(output_dir=output_dir)
    _not_implemented("report", config)


if __name__ == "__main__":
    app()
