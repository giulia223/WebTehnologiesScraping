"""Command-line entry point: `techdetect <command>`.

The commands mirror the pipeline: collect (network) -> detect (offline) -> evaluate ->
report, plus `rules` to inspect the fingerprint rules. Commands not built yet are stubs.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from techdetect.collect.browser import collect_browser
from techdetect.collect.dns_probe import collect_dns
from techdetect.collect.run import (
    RUN_COMPLETE,
    STEPS,
    Collector,
    FinishedRunError,
    parse_steps,
    run_collect,
)
from techdetect.collect.static_fetch import collect_static
from techdetect.collect.storage import write_json_atomic
from techdetect.collect.tls_probe import collect_tls
from techdetect.collect.wellknown import collect_wellknown
from techdetect.config import Config
from techdetect.console import use_utf8_output
from techdetect.detect import (
    NotFinishedError,
    check_run_complete,
    default_workers,
    detect_all,
    write_outputs,
)
from techdetect.domains import Domain, LoadResult, load_domains
from techdetect.evaluate import ablation
from techdetect.evaluate.importer import import_blind, parse_inspection_file
from techdetect.evaluate.labels import (
    REVIEW_SEED,
    blind_domains,
    load_ground_truth,
    pick_review,
    read_all_detections,
    save_ground_truth,
)
from techdetect.evaluate.metrics import compute, rate
from techdetect.evaluate.metrics_report import render
from techdetect.evaluate.session import label_domain
from techdetect.fingerprints.candidates import promote
from techdetect.fingerprints.loader import load_rules
from techdetect.fingerprints.matcher import INLINE_URLS, SOURCES
from techdetect.fingerprints.review import candidate_summary, review_candidates

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
RawDirOpt = Annotated[
    Path | None, typer.Option("--raw-dir", "--raw", help="Raw artifacts folder (data/raw).")
]
# For collect the raw folder is its output; --raw-dir is kept as an alias.
CollectOutOpt = Annotated[
    Path | None, typer.Option("--out", "--raw-dir", help="Raw artifacts folder (data/raw).")
]
OutputDirOpt = Annotated[Path | None, typer.Option("--output-dir", help="Results folder.")]
RulesDirOpt = Annotated[Path | None, typer.Option("--rules-dir", help="Fingerprint rules folder.")]
CandidatesOpt = Annotated[
    bool,
    typer.Option(
        "--include-candidates",
        help="Also load candidate rules marked proposed or accepted (never rejected).",
    ),
]
ConcurrencyOpt = Annotated[
    int | None,
    typer.Option(
        "--concurrency", min=1, help="Sites fetched in parallel (default 10; browser: 4)."
    ),
]
TimeoutOpt = Annotated[
    float | None, typer.Option("--timeout", min=0.1, help="Static fetch timeout in seconds.")
]
UserAgentOpt = Annotated[str | None, typer.Option("--user-agent", help="HTTP User-Agent.")]
DryRunOpt = Annotated[
    bool,
    typer.Option("--dry-run", help="Load and validate the input, list the domains, fetch nothing."),
]
OnlyOpt = Annotated[
    str | None,
    typer.Option(
        "--only",
        help=f"Comma-separated steps to run ({','.join(STEPS)}); default: all. "
        "tls and wellknown use static.json, so static runs first.",
    ),
]
LimitOpt = Annotated[
    int | None, typer.Option("--limit", min=1, help="Use only the first N valid domains.")
]
OverwriteOpt = Annotated[
    bool,
    typer.Option(
        "--overwrite",
        help="Collect into a folder that already holds a finished run; only the chosen "
        "steps' files are replaced.",
    ),
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
    raw_dir: CollectOutOpt = None,
    concurrency: ConcurrencyOpt = None,
    static_timeout_s: TimeoutOpt = None,
    user_agent: UserAgentOpt = None,
    dry_run: DryRunOpt = False,
    only: OnlyOpt = None,
    limit: LimitOpt = None,
    overwrite: OverwriteOpt = False,
) -> None:
    """Fetch raw artifacts for each domain (network)."""
    config = Config().with_overrides(
        input_path=input_path,
        raw_dir=raw_dir,
        concurrency=concurrency,
        # One flag for both limits: whoever sets it wants that many sites at once.
        browser_concurrency=concurrency,
        static_timeout_s=static_timeout_s,
        user_agent=user_agent,
    )
    try:
        steps = parse_steps(only)
    except ValueError as exc:
        typer.echo(f"--only {only}: {exc}", err=True)
        raise typer.Exit(code=1) from None

    loaded = _load_and_report(config)
    domains = loaded.domains[:limit] if limit else loaded.domains
    if dry_run:
        for domain in domains:
            # Show the decoded name only when it differs, to keep the list readable.
            shown = f"  ({domain.display_name})" if domain.display_name != domain.host else ""
            typer.echo(f"{domain.row:>4}  {domain.host}{shown}")
        return

    typer.echo(f"steps: {', '.join(steps)} -> {config.raw_dir}")
    try:
        complete = asyncio.run(
            run_collect(
                domains,
                config,
                steps,
                _collectors(),
                overwrite=overwrite,
                input_label=str(config.input_path),
                limit=limit,
                progress=_print_progress,
            )
        )
    except FinishedRunError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    _print_summary(complete)
    typer.echo(f"run complete: {config.raw_dir / RUN_COMPLETE}")


def _collectors() -> dict[str, Collector]:
    # Looked up at call time (not stored in a module-level dict) so tests can replace one.
    return {
        "static": collect_static,
        "dns": collect_dns,
        "tls": collect_tls,
        "wellknown": collect_wellknown,
        "browser": collect_browser,
    }


def _print_progress(step: str, domain: Domain, result: dict[str, Any], duration_ms: int) -> None:
    status = result.get("status", "?")
    try:
        detail = result.get("error") if status == "crashed" else _detail(step, result)
    except (KeyError, TypeError, IndexError):
        # A progress line must never stop a crawl; the full result is on disk anyway.
        detail = ""
    typer.echo(f"{step:9} {domain.host:45} {status:8} {duration_ms / 1000:6.1f}s  {detail}")


def _detail(step: str, result: dict[str, Any]) -> str:
    """One short line per domain, enough to spot a problem by eye."""
    if step in ("static", "browser"):
        if result["status"] == "ok":
            return result["final_url"]
        # The reason first; the status code and URL help to check it by hand.
        status = result.get("http_status") or "-"
        return f"{result['error_category']} (HTTP {status}) {result.get('final_url') or ''}"
    if step == "dns":
        answered = [q for q in result["queries"] if q["status"] == "ok"]
        return f"{len(answered)}/{len(result['queries'])} queries answered"
    if step == "tls":
        if result["status"] != "ok":
            return f"{result['error_category']} {result['probed_host']}"
        issuer = dict(result["certificate"]["issuer"] or []).get("organizationName", "?")
        return (
            f"{result['probed_host']} verified={result['chain_verified']} "
            f"match={result['hostname_match']} issuer={issuer}"
        )
    robots = result["robots"]
    if robots["outcome"] == "failed":
        return f"{robots['error_category']} {robots['url']}"
    reason = f" {robots['reason']}" if robots["reason"] else ""
    return f"{robots['outcome']}{reason} (HTTP {robots['http_status']}) {robots['url']}"


def _print_summary(complete: dict[str, Any]) -> None:
    """Success and failure per step and per error category, as read back from disk."""
    table = Table(title=f"collect: {complete['domains']} domains (counts read from disk)")
    for column in ("step", "ok", "failed", "missing", "failures by category", "avg s/domain"):
        table.add_column(
            column, justify="left" if column in ("step", "failures by category") else "right"
        )
    averages = complete["avg_seconds_per_domain"]
    for step, counts in complete["counts"].items():
        categories = ", ".join(f"{c} {n}" for c, n in counts["by_category"].items()) or "-"
        ran_now = step in complete["steps_run_now"]
        table.add_row(
            step if ran_now else f"{step} (earlier run)",
            str(counts["ok"]),
            str(counts["failed"]),
            str(counts["missing"]),
            categories,
            f"{averages[step]:.2f}" if step in averages else "-",
        )
    Console().print(table)


@app.command()
def rules(rules_dir: RulesDirOpt = None, include_candidates: CandidatesOpt = False) -> None:
    """Load the fingerprint rules and print the load report (offline)."""
    config = Config().with_overrides(rules_dir=rules_dir)
    ruleset = load_rules(config.rules_dir, include_candidates=include_candidates)
    typer.echo(ruleset.report.format(ruleset.version))


candidates_app = typer.Typer(
    help="Proposed rules: review them, then promote the accepted ones into custom/.",
    no_args_is_help=True,
)
app.add_typer(candidates_app, name="candidates")


@candidates_app.command("review")
def candidates_review(rules_dir: RulesDirOpt = None, raw_dir: RawDirOpt = None) -> None:
    """Accept or reject each proposed rule, seeing what it matches on saved data."""
    config = Config().with_overrides(rules_dir=rules_dir, raw_dir=raw_dir)
    base = load_rules(config.rules_dir)  # for category names
    review_candidates(config.rules_dir / "candidates", base, config.raw_dir)


@candidates_app.command("promote")
def candidates_promote(rules_dir: RulesDirOpt = None) -> None:
    """Move only `accepted` rules into custom/, stamped reviewed_by: human and dated."""
    config = Config().with_overrides(rules_dir=rules_dir)
    try:
        moved = promote(config.rules_dir / "candidates", config.rules_dir / "custom")
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    for name in moved:
        typer.echo(f"promoted {name}")
    left = candidate_summary(config.rules_dir / "candidates")
    typer.echo(f"{len(moved)} promoted; still in candidates/: {left or 'nothing'}")


AllowPartialOpt = Annotated[
    bool,
    typer.Option(
        "--allow-partial",
        help="Run on a raw folder whose collection did not finish (summary.json says so).",
    ),
]
InlineUrlsOpt = Annotated[
    bool,
    typer.Option(
        "--inline-urls",
        help="Also match script-URL rules against URLs inside the static page's inline "
        "scripts (tracker loader snippets). Off by default.",
    ),
]
MinConfidenceOpt = Annotated[
    int,
    typer.Option(
        "--min-confidence",
        min=0,
        max=100,
        help="Count only detections at or above this confidence (default 0: all, "
        "as Wappalyzer). detections.jsonl always keeps every detection.",
    ),
]
WorkersOpt = Annotated[
    int | None,
    typer.Option("--workers", min=1, help="Processes matching domains in parallel."),
]


@app.command()
def detect(
    input_path: InputOpt = None,
    raw_dir: RawDirOpt = None,
    output_dir: OutputDirOpt = None,
    rules_dir: RulesDirOpt = None,
    include_candidates: CandidatesOpt = False,
    allow_partial: AllowPartialOpt = False,
    min_confidence: MinConfidenceOpt = 0,
    workers: WorkersOpt = None,
    inline_urls: InlineUrlsOpt = False,
) -> None:
    """Find technologies in saved raw artifacts (offline)."""
    config = Config().with_overrides(
        input_path=input_path, raw_dir=raw_dir, output_dir=output_dir, rules_dir=rules_dir
    )
    try:
        complete = check_run_complete(config.raw_dir, allow_partial)
    except NotFinishedError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None

    domains = _load_and_report(config).domains
    ruleset = load_rules(config.rules_dir, include_candidates=include_candidates)
    started = time.perf_counter()
    lines = detect_all(
        config.raw_dir,
        domains,
        config.rules_dir,
        include_candidates=include_candidates,
        workers=workers or default_workers(),
        sources=SOURCES | {INLINE_URLS} if inline_urls else SOURCES,
    )
    outputs = write_outputs(
        lines, ruleset, config.output_dir, min_confidence, not complete, inline_urls
    )
    unique = outputs.summary["unique_technologies"]
    per_domain = outputs.summary["core_per_domain"]
    typer.echo(
        f"{len(lines)} domains in {time.perf_counter() - started:.0f} s; "
        f"core technologies: {unique['core']}, extended total: {unique['extended_total']} "
        f"({', '.join(f'{g} {n}' for g, n in unique['separately'].items())})"
    )
    typer.echo(
        f"average core technologies per domain: {per_domain['average']} "
        f"({per_domain['denominator']}); outputs in {config.output_dir}"
    )


evaluate_app = typer.Typer(
    help="Hand labels (ground truth), precision and recall.", no_args_is_help=True
)
app.add_typer(evaluate_app, name="evaluate")
GroundTruthOpt = Annotated[
    Path | None, typer.Option("--ground-truth", help="Labels file (data/ground_truth.json).")
]


@evaluate_app.command("pick-review")
def pick_review_command(
    input_path: InputOpt = None,
    output_dir: OutputDirOpt = None,
    ground_truth: GroundTruthOpt = None,
    seed: Annotated[int, typer.Option("--seed", help="Random seed for the pick.")] = REVIEW_SEED,
    force: Annotated[
        bool, typer.Option("--force", help="Draw again over an existing pick.")
    ] = False,
) -> None:
    """Set the blind set (every 20th row) and draw the review set (random, fixed seed)."""
    config = Config().with_overrides(
        input_path=input_path, output_dir=output_dir, ground_truth_path=ground_truth
    )
    data = load_ground_truth(config.ground_truth_path)
    if data["sets"]["review"] and not force:
        typer.echo(
            f"review set already picked: {', '.join(data['sets']['review'])} "
            "(use --force to draw again)",
            err=True,
        )
        raise typer.Exit(code=1)
    data["sets"]["blind"] = blind_domains(_load_and_report(config).domains)
    data["sets"]["review"] = pick_review(
        read_all_detections(config.output_dir), data["sets"]["blind"], seed
    )
    data["review_seed"] = seed
    save_ground_truth(config.ground_truth_path, data)
    typer.echo(f"blind ({len(data['sets']['blind'])}): {', '.join(data['sets']['blind'])}")
    typer.echo(f"review ({len(data['sets']['review'])}): {', '.join(data['sets']['review'])}")
    typer.echo(f"saved in {config.ground_truth_path}")


@evaluate_app.command("import-blind")
def import_blind_command(
    inspection: Annotated[
        Path, typer.Option("--file", help="The blind inspection notes (Task 0.3).")
    ] = Path("docs/manual_inspect_domains.md"),
    ground_truth: GroundTruthOpt = None,
    rules_dir: RulesDirOpt = None,
    force: Annotated[
        bool, typer.Option("--force", help="Replace lists already in the ground truth.")
    ] = False,
) -> None:
    """Read the blind domains' technology lists from the inspection notes."""
    config = Config().with_overrides(ground_truth_path=ground_truth, rules_dir=rules_dir)
    data = load_ground_truth(config.ground_truth_path)
    if not data["sets"]["blind"]:
        typer.echo("No sets yet: run `techdetect evaluate pick-review` first.", err=True)
        raise typer.Exit(code=1)
    names = sorted(load_rules(config.rules_dir).technologies)
    parsed = parse_inspection_file(inspection.read_text(encoding="utf-8"))
    for domain, outcome in import_blind(data, parsed, names, force).items():
        typer.echo(f"{domain}: {outcome}")
        for entry in data["domains"].get(domain, {}).get("human_found", []):
            if not entry["in_rules"] and outcome.startswith("imported"):
                hint = (
                    f" (close: {', '.join(entry['suggestions'][:3])})"
                    if entry["suggestions"]
                    else ""
                )
                typer.echo(f"   not in the rules: {entry['technology']}{hint}")
    save_ground_truth(config.ground_truth_path, data)
    typer.echo(f"saved in {config.ground_truth_path}")


@evaluate_app.command("label")
def label_command(
    domain: Annotated[str, typer.Argument(help="The domain to label, as in the input list.")],
    output_dir: OutputDirOpt = None,
    ground_truth: GroundTruthOpt = None,
    rules_dir: RulesDirOpt = None,
    relabel: Annotated[
        bool, typer.Option("--relabel", help="Ask again for detections already labelled.")
    ] = False,
) -> None:
    """Mark each detection of a domain correct / wrong / unsure, and add missed ones."""
    config = Config().with_overrides(
        output_dir=output_dir, ground_truth_path=ground_truth, rules_dir=rules_dir
    )
    data = load_ground_truth(config.ground_truth_path)
    if not data["sets"]["blind"]:
        typer.echo("No sets yet: run `techdetect evaluate pick-review` first.", err=True)
        raise typer.Exit(code=1)
    names = sorted(load_rules(config.rules_dir).technologies)
    label_domain(domain, config.ground_truth_path, config.output_dir, names, relabel=relabel)


@evaluate_app.command("metrics")
def metrics_command(
    output_dir: OutputDirOpt = None,
    ground_truth: GroundTruthOpt = None,
    report_path: Annotated[
        Path, typer.Option("--report", help="Where to write the readable report.")
    ] = Path("docs/METRICS.md"),
) -> None:
    """Precision, recall and F1 from the labels; every false positive with its evidence."""
    config = Config().with_overrides(output_dir=output_dir, ground_truth_path=ground_truth)
    data = load_ground_truth(config.ground_truth_path)
    if not data["sets"]["blind"]:
        typer.echo("No sets yet: run `techdetect evaluate pick-review` first.", err=True)
        raise typer.Exit(code=1)
    lines = {line["domain"]: line for line in read_all_detections(config.output_dir)}
    metrics = compute(data, lines)
    write_json_atomic(config.output_dir / "metrics.json", metrics)
    report_path.write_text(render(metrics), encoding="utf-8")
    for h in metrics["headline"]:
        f1 = f"{h['f1']:.2f}" if h["f1"] is not None else "n/a"
        typer.echo(
            f"threshold {h['threshold']:>2}: precision {rate(*h['precision'])}, "
            f"recall {rate(*h['recall'])}, F1 {f1}, unsure {h['unsure']}"
        )
    if metrics["unlabelled"] or metrics["stale"]:
        typer.echo(
            f"warning: {len(metrics['unlabelled'])} detections without a verdict, "
            f"{len(metrics['stale'])} verdicts for detections no longer reported",
            err=True,
        )
    typer.echo(f"wrote {report_path} and {config.output_dir / 'metrics.json'}")


@evaluate_app.command("ablation")
def ablation_command(
    input_path: InputOpt = None,
    raw_dir: RawDirOpt = None,
    output_dir: OutputDirOpt = None,
    rules_dir: RulesDirOpt = None,
    ground_truth: GroundTruthOpt = None,
    workers: WorkersOpt = None,
    report_path: Annotated[
        Path, typer.Option("--report", help="Where to write the readable report.")
    ] = Path("docs/ABLATION.md"),
) -> None:
    """Re-run detection with fewer collection steps: what each step adds, and its cost."""
    config = Config().with_overrides(
        input_path=input_path,
        raw_dir=raw_dir,
        output_dir=output_dir,
        rules_dir=rules_dir,
        ground_truth_path=ground_truth,
    )
    domains = _load_and_report(config).domains
    ruleset = load_rules(config.rules_dir)
    started = time.perf_counter()
    result = ablation.run_ablation(
        config.raw_dir,
        domains,
        config.rules_dir,
        ruleset,
        load_ground_truth(config.ground_truth_path),
        workers or default_workers(),
    )
    summary = json.loads((config.output_dir / "summary.json").read_text(encoding="utf-8"))
    report_path.write_text(ablation.render(result, ruleset, summary), encoding="utf-8")
    write_json_atomic(config.output_dir / "ablation.json", result)
    for row in result["rows"]:
        typer.echo(
            f"{row['label']:<10} core {row['unique_core']}, extended {row['unique_extended']}, "
            f"precision {rate(*row['precision'])}, recall {rate(*row['recall'])}, "
            f"{row['cost_s_per_domain']} s/domain"
        )
    typer.echo(f"{time.perf_counter() - started:.0f} s; wrote {report_path}")
    problem = ablation.check_against_summary(result, summary)
    if problem:
        typer.echo(f"check failed: {problem}", err=True)
        raise typer.Exit(code=1)


@app.command()
def report(output_dir: OutputDirOpt = None) -> None:
    """Write the final tables and summaries."""
    config = Config().with_overrides(output_dir=output_dir)
    _not_implemented("report", config)


if __name__ == "__main__":
    app()
