"""Command-line entry point: `techdetect <command>`.

The four commands mirror the pipeline: collect (network) -> detect (offline)
-> evaluate -> report. They are stubs until their tasks are implemented.
"""

from pathlib import Path
from typing import Annotated

import typer

from techdetect.config import Config
from techdetect.console import use_utf8_output
from techdetect.domains import LoadResult, load_domains

app = typer.Typer(
    help="Detect website technologies with evidence.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Detect website technologies with evidence."""
    # Runs before every command, so no command can crash printing a decoded IDN name.
    use_utf8_output()

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
DryRunOpt = Annotated[
    bool,
    typer.Option("--dry-run", help="Load and validate the input, list the domains, fetch nothing."),
]


def _not_implemented(command: str, config: Config) -> None:
    # Exit code 1, not 0: a script calling a stub must not believe it succeeded.
    typer.echo(f"{command}: not implemented yet")
    raise typer.Exit(code=1)


def _load_and_report(config: Config) -> LoadResult:
    """Load the input; report every invalid row on stderr and continue with the valid ones."""
    loaded = load_domains(config.input_path)
    for bad in loaded.invalid:
        typer.echo(f"invalid row {bad.row}: {bad.value!r}: {bad.reason}", err=True)
    typer.echo(
        f"{config.input_path}: {len(loaded.domains)} valid domains, "
        f"{len(loaded.invalid)} invalid rows"
    )
    return loaded


@app.command()
def collect(
    input_path: InputOpt = None,
    raw_dir: RawDirOpt = None,
    concurrency: ConcurrencyOpt = None,
    static_timeout_s: TimeoutOpt = None,
    user_agent: UserAgentOpt = None,
    dry_run: DryRunOpt = False,
) -> None:
    """Fetch raw artifacts for each domain (network)."""
    config = Config().with_overrides(
        input_path=input_path,
        raw_dir=raw_dir,
        concurrency=concurrency,
        static_timeout_s=static_timeout_s,
        user_agent=user_agent,
    )
    loaded = _load_and_report(config)
    if dry_run:
        for domain in loaded.domains:
            # Show the decoded name only when it differs, to keep the list readable.
            shown = f"  ({domain.display_name})" if domain.display_name != domain.host else ""
            typer.echo(f"{domain.row:>4}  {domain.host}{shown}")
        return
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
