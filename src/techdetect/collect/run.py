"""One `collect` run: every step over every domain, a run log, and a completion marker.

Steps run one after another over all domains, in a fixed order: static first, because
the TLS and robots.txt steps read static.json to know the site's final address.

There is no resume. An interrupted run is simply run again from the start; three things
keep that safe:
  - every file is written atomically (a JSON on disk means its step finished);
  - `_run_complete.json` is written only at the very end, so a folder without it is an
    unfinished run;
  - a folder that already holds a finished run is never collected into again unless
    --overwrite is given, so a crashed re-run cannot destroy a good dataset.
"""

import json
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from techdetect.collect.provenance import tool_version
from techdetect.collect.runner import OnResult
from techdetect.collect.storage import domain_dir, write_json_atomic
from techdetect.config import Config
from techdetect.domains import Domain

# Order matters: tls and wellknown use static.json; browser fills nothing others need.
STEPS = ("static", "dns", "tls", "wellknown", "browser")
STEP_FILES = {step: f"{step}.json" for step in STEPS}
RUN_COMPLETE = "_run_complete.json"
RUN_LOG = "_run_log.jsonl"

Collector = Callable[..., Awaitable[list[dict[str, Any]]]]
# Called for every finished (step, domain): lets the CLI print progress.
Progress = Callable[[str, Domain, dict[str, Any], int], None]


class FinishedRunError(Exception):
    """The output folder already holds a finished run and --overwrite was not given."""


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_steps(only: str | None) -> list[str]:
    """ "static,dns" -> ["static", "dns"], always in run order; None = every step."""
    if only is None:
        return list(STEPS)
    asked = {part.strip() for part in only.split(",") if part.strip()}
    unknown = asked - set(STEPS)
    if unknown or not asked:
        raise ValueError(f"unknown step(s) {sorted(unknown)}; steps are {list(STEPS)}")
    return [step for step in STEPS if step in asked]


def step_outcome(step: str, result: dict[str, Any]) -> tuple[str, str | None]:
    """(status, error category) of one step for one domain, from its JSON.

    Each step records failure a little differently; this is the one place that knows how.
    """
    status = result.get("status", "missing")
    if status != "failed":
        return status, None
    if step == "dns":
        # No query answered: name the most common reason (nxdomain, timeout, ...).
        reasons = Counter(q["status"] for q in result.get("queries", []))
        return status, reasons.most_common(1)[0][0] if reasons else None
    if step == "wellknown":
        return status, result.get("robots", {}).get("error_category")
    return status, result.get("error_category")


def read_step(raw_dir: Path, host: str, step: str) -> dict[str, Any] | None:
    path = domain_dir(raw_dir, host) / STEP_FILES[step]
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def disk_summary(raw_dir: Path, hosts: list[str], steps: list[str]) -> dict[str, Any]:
    """Counts per step, read back from the files on disk (not from memory): this is what
    detection will actually find. A missing file = the step crashed or never ran."""
    summary: dict[str, Any] = {}
    for step in steps:
        counts: Counter[str] = Counter()
        categories: Counter[str] = Counter()
        for host in hosts:
            data = read_step(raw_dir, host, step)
            status, category = step_outcome(step, data) if data else ("missing", None)
            counts[status] += 1
            if category:
                categories[category] += 1
        summary[step] = {
            "ok": counts["ok"],
            "failed": counts["failed"],
            "missing": counts["missing"],
            "by_category": dict(categories.most_common()),
        }
    return summary


def fill_final_url_differs(raw_dir: Path, host: str) -> None:
    """Write browser.json's `final_url_differs_from_static` once both steps are on disk.

    True means the browser ended somewhere else than the plain HTTP fetch, usually a
    redirect done by JavaScript. None when either step did not reach a real page.
    """
    static = read_step(raw_dir, host, "static")
    browser = read_step(raw_dir, host, "browser")
    if browser is None:
        return
    differs = None
    if static and static.get("status") == "ok" and browser.get("status") == "ok":
        differs = static.get("final_url") != browser.get("final_url")
    if browser.get("final_url_differs_from_static") != differs:
        browser["final_url_differs_from_static"] = differs
        write_json_atomic(domain_dir(raw_dir, host) / STEP_FILES["browser"], browser)


class RunLog:
    """`_run_log.jsonl`: one line per (domain, step), appended as each one finishes."""

    def __init__(self, path: Path, run_started_at: str) -> None:
        self.run_started_at = run_started_at
        self.file: TextIO = path.open("a", encoding="utf-8")

    def write(self, step: str, domain: Domain, result: dict[str, Any], duration_ms: int) -> None:
        status, category = step_outcome(step, result)
        line = {
            "run_started_at": self.run_started_at,  # several runs can share one log
            "domain": domain.host,
            "step": step,
            "status": status,
            "duration_ms": duration_ms,
            "error_category": category,
        }
        if status == "crashed":
            line["error"] = result.get("error")
        self.file.write(json.dumps(line, ensure_ascii=False) + "\n")
        self.file.flush()  # visible at once, also if the run is interrupted

    def close(self) -> None:
        self.file.close()


def read_run_log(path: Path, run_started_at: str) -> list[dict[str, Any]]:
    """This run's lines of the log."""
    if not path.is_file():
        return []
    lines = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line.get("run_started_at") == run_started_at]


def average_seconds(log_lines: list[dict[str, Any]], steps: list[str]) -> dict[str, float]:
    """Average time per domain per step in this run (each domain timed on its own)."""
    out = {}
    for step in steps:
        times = [line["duration_ms"] for line in log_lines if line["step"] == step]
        if times:
            out[step] = round(sum(times) / len(times) / 1000, 2)
    return out


async def run_collect(
    domains: list[Domain],
    config: Config,
    steps: list[str],
    collectors: dict[str, Collector],
    overwrite: bool = False,
    input_label: str = "",
    limit: int | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Run `steps` over `domains` into config.raw_dir; returns the _run_complete.json content."""
    out = config.raw_dir
    marker = out / RUN_COMPLETE
    previous = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else None
    if previous and not overwrite:
        raise FinishedRunError(
            f"{out} already holds a finished run (finished {previous.get('finished_at')}). "
            "Use --overwrite to re-run the chosen steps there, or choose another --out."
        )
    out.mkdir(parents=True, exist_ok=True)
    if previous:
        # From now until the end this folder is an unfinished run again.
        marker.unlink()

    started_at = now()
    log = RunLog(out / RUN_LOG, started_at)
    step_times: dict[str, dict[str, str]] = {}
    try:
        for step in steps:
            on_result = _on_result_for(step, out, log, progress)
            step_started = now()
            await collectors[step](domains, config, on_result=on_result)
            step_times[step] = {"started_at": step_started, "finished_at": now()}
    finally:
        log.close()

    hosts = [d.host for d in domains]
    if "static" in steps or "browser" in steps:
        for host in hosts:
            fill_final_url_differs(out, host)

    # Steps run earlier (kept from a previous finished run) stay listed with their dates.
    all_steps_run = dict((previous or {}).get("steps", {}))
    for step, times in step_times.items():
        all_steps_run[step] = {**times, "tool_version": tool_version()}
    ordered = [s for s in STEPS if s in all_steps_run]
    complete = {
        "started_at": started_at,
        "finished_at": now(),
        "tool_version": tool_version(),
        "input": input_label,
        "limit": limit,
        "domains": len(domains),
        "steps_run_now": steps,
        "steps": {s: all_steps_run[s] for s in ordered},
        "counts": disk_summary(out, hosts, ordered),
        "avg_seconds_per_domain": average_seconds(read_run_log(out / RUN_LOG, started_at), steps),
        "previous_runs": [
            *(previous or {}).get("previous_runs", []),
            *([_short(previous)] if previous else []),
        ],
    }
    write_json_atomic(marker, complete)  # last: from here on the folder is a finished run
    return complete


def _short(run: dict[str, Any]) -> dict[str, Any]:
    keys = ("started_at", "finished_at", "tool_version", "input", "limit", "steps_run_now")
    return {k: run.get(k) for k in keys}


def _on_result_for(step: str, out: Path, log: RunLog, progress: Progress | None) -> OnResult:
    def on_result(domain: Domain, result: dict[str, Any], duration_ms: int) -> None:
        if result.get("status") == "crashed":
            # A file from an older run must not be counted as this run's result.
            stale = domain_dir(out, domain.host) / STEP_FILES[step]
            if stale.exists():
                stale.unlink()
        log.write(step, domain, result, duration_ms)
        if progress is not None:
            progress(step, domain, result, duration_ms)

    return on_result
