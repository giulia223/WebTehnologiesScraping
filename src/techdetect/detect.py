"""`detect`: turn the collected raw artifacts into technology detections, offline.

For every input domain: match the rules against data/raw/<domain>/, combine the matches
per technology, apply implies/excludes/requires, and give the domain a status. Writes:

  detections.jsonl         one line per input domain (failed ones too), with evidence
  technologies.csv/.parquet one row per (domain, technology)
  summary.json             the counts: core and every separate group, failures, coverage

Counting rule (name level, no versions): a technology counts once however many domains
use it. "Core" = technologies with observed or declared evidence from the website itself,
plugins and apps included (for this tool's users, e.g. a Shopify analytics vendor, they
are the competing products). Reported separately, never mixed in: technologies only ever
inferred, only ever seen in DNS, and themes (mostly a site's look, not what it can do).
"""

import csv
import io
import json
import os
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pyarrow as pa
import pyarrow.parquet as pq

from techdetect.collect.provenance import tool_version
from techdetect.collect.run import RUN_COMPLETE
from techdetect.collect.storage import write_atomic, write_json_atomic
from techdetect.domains import Domain, registered_domain
from techdetect.fingerprints.combine import PatternGroup, TechnologyResult, combine
from techdetect.fingerprints.loader import RuleSet, load_rules
from techdetect.fingerprints.matcher import RuleIndex, match_domain
from techdetect.fingerprints.resolve import resolve

# Themes are reported as their own group, not in core: a theme is mostly the site's look
# and tells little about what the site can do. A technology is a theme here only if it
# has a theme category and nothing but theme or plugin categories: Divi and Bricks are
# also "Page builders", so they stay in core. Plugins and apps (WordPress plugins 87,
# Shopify apps 100) are technologies like any other and count in core.
THEME_CATEGORIES = frozenset({80, 81, 82})  # WordPress, Shopify, Drupal themes
PLUGIN_CATEGORIES = frozenset({87, 100})  # WordPress plugins, Shopify apps
# Name-level precedence: website evidence on any domain makes a technology core; seen in
# DNS beats only inferred.
GROUPS = ("core", "dns_only", "inferred_only", "themes")

# Domain status (F3.6): the categories a failed domain can have.
_FAILURE_NAMES = {
    "dns_fail": "dns_error",
    "timeout": "timeout",
    "tls_error": "tls_error",
    "conn_refused": "connection_error",
    "conn_error": "connection_error",
    "browser_error": "connection_error",
    "http_4xx": "http_error",
    "http_5xx": "http_error",
    "empty_response": "http_error",
    "redirect_loop": "http_error",
    "rate_limited": "rate_limited",
}
# When both fetches failed, the most informative reason wins (as in the static fetch):
# a challenge page (the site exists) > an HTTP answer > no answer at all.
_INFORMATIVENESS = {"bot_challenge": 0, "http_error": 1, "rate_limited": 1}


class NotFinishedError(Exception):
    """The raw folder has no _run_complete.json and --allow-partial was not given."""


# ---------------------------------------------------------------- per domain


def _read_json(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def domain_status(static: dict | None, browser: dict | None) -> str:
    """ok (either fetch got a real page) / bot_challenge / failed:<category>."""
    pages = [p for p in (static, browser) if p is not None]
    if not pages:
        return "failed:not_collected"
    if any(p.get("status") == "ok" for p in pages):
        return "ok"
    reasons = [
        "bot_challenge"
        if p.get("error_category") == "bot_challenge"
        else _FAILURE_NAMES.get(p.get("error_category") or "", "connection_error")
        for p in pages
    ]
    best = min(reasons, key=lambda r: _INFORMATIVENESS.get(r, 2))  # ties: static first
    return best if best == "bot_challenge" else f"failed:{best}"


def redirected_to(host: str, static: dict | None, browser: dict | None) -> str | None:
    """The final page's registered domain, when it is not the input's (www. and
    http -> https do not count). The browser's final URL is preferred: it also follows
    JavaScript redirects, so it is where a visitor really ends up."""
    for page in (browser, static):
        if page and page.get("status") == "ok" and page.get("final_url"):
            final = registered_domain(urlsplit(page["final_url"]).hostname or "")
            return final if final != registered_domain(host) else None
    return None


def counting_group(result: TechnologyResult, ruleset: RuleSet) -> str:
    """Which count a detection belongs to on this domain."""
    categories = set(ruleset.technologies[result.name].categories)
    if categories & THEME_CATEGORIES and categories <= THEME_CATEGORIES | PLUGIN_CATEGORIES:
        return "themes"  # e.g. Astra, Twenty Twenty-Four, whatever evidence it has
    if result.tier == "inferred":
        return "inferred_only"
    if {g.signal for g in result.groups} == {"dns"}:
        return "dns_only"
    return "core"


def _group_json(group: PatternGroup) -> dict[str, Any]:
    return {
        "signal": group.signal,
        "key": group.key,
        "subkey": group.subkey,
        "pattern": group.pattern,
        "confidence": group.confidence,
        "tier": group.tier,
        "match_count": group.match_count,
        "artifacts": list(group.artifacts),
        "versions": list(group.versions),
        "examples": [
            {
                "source_artifact": e.source_artifact,
                "location": e.location,
                "matched_value": e.matched_value,
                "context": e.context,
            }
            for e in group.examples
        ],
    }


def _technology_json(result: TechnologyResult, ruleset: RuleSet) -> dict[str, Any]:
    categories = ruleset.technologies[result.name].categories
    return {
        "name": result.name,
        "categories": [ruleset.categories[c].name for c in categories if c in ruleset.categories],
        "confidence": result.confidence,
        "versions": list(result.versions),
        "tier": result.tier,
        "group": counting_group(result, ruleset),
        "implied_by": result.implied_by,
        "signals": sorted({g.signal for g in result.groups}),
        "evidence": [_group_json(g) for g in result.groups],
    }


def detect_domain(raw_dir: Path, domain: Domain, ruleset: RuleSet, index: RuleIndex) -> dict:
    """One line of detections.jsonl: the domain's status and every detection with proof."""
    folder = raw_dir / domain.host
    static = _read_json(folder / "static.json")
    browser = _read_json(folder / "browser.json")
    status = domain_status(static, browser)
    line: dict[str, Any] = {
        "domain": domain.host,
        "row": domain.row,
        "display_name": domain.display_name,
        "status": status,
        "redirected_to": redirected_to(domain.host, static, browser),
        "technologies": [],
    }
    if status == "failed:not_collected":
        return line
    matches = match_domain(raw_dir, domain.host, index)
    resolution = resolve(combine(matches.evidence), ruleset)
    line["technologies"] = [
        _technology_json(result, ruleset) for result in resolution.technologies.values()
    ]
    line["excluded"] = resolution.excluded
    line["unmet_requirements"] = {k: list(v) for k, v in resolution.unmet_requirements.items()}
    line["artifacts"] = matches.artifacts
    line["not_checked"] = dict(matches.not_checked)
    line["regex_timeouts"] = [list(t) for t in matches.timeouts]
    return line


# ---------------------------------------------------------------- many domains


_worker_rules: tuple[RuleSet, RuleIndex] | None = None


def _init_worker(rules_dir: str, include_candidates: bool) -> None:
    # Each worker process loads the rules once (about 1-2 s), then matches many domains.
    global _worker_rules
    ruleset = load_rules(Path(rules_dir), include_candidates=include_candidates)
    _worker_rules = (ruleset, RuleIndex(ruleset))


def _work(args: tuple[str, Domain]) -> dict:
    assert _worker_rules is not None
    raw_dir, domain = args
    return detect_domain(Path(raw_dir), domain, *_worker_rules)


def detect_all(
    raw_dir: Path,
    domains: list[Domain],
    rules_dir: Path,
    include_candidates: bool = False,
    workers: int = 1,
    progress: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Every domain's line, in input order. Domains are independent, so with workers > 1
    they are matched in parallel processes (matching is CPU work, about 1 s per domain)."""
    jobs = [(str(raw_dir), d) for d in domains]
    if workers <= 1:
        _init_worker(str(rules_dir), include_candidates)
        lines = []
        for job in jobs:
            lines.append(_work(job))
            if progress:
                progress(lines[-1])
        return lines
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        initargs=(str(rules_dir), include_candidates),
    ) as pool:
        lines = []
        for line in pool.map(_work, jobs, chunksize=4):
            lines.append(line)
            if progress:
                progress(line)
        return lines


# ---------------------------------------------------------------- outputs


@dataclass(frozen=True)
class Outputs:
    lines: list[dict]
    summary: dict[str, Any]


def _rows(lines: list[dict], threshold: int) -> list[dict[str, Any]]:
    return [
        {
            "domain": line["domain"],
            "technology": tech["name"],
            "categories": "; ".join(tech["categories"]),
            # Several versions on one site are all real (e.g. two jQuery copies).
            "version": "; ".join(tech["versions"]),
            "confidence": tech["confidence"],
            "tier": tech["tier"],
            "group": tech["group"],
            "signals": "; ".join(tech["signals"]),
        }
        for line in lines
        for tech in line["technologies"]
        if tech["confidence"] >= threshold
    ]


def _write_tables(rows: list[dict[str, Any]], output_dir: Path) -> None:
    columns = [
        "domain",
        "technology",
        "categories",
        "version",
        "confidence",
        "tier",
        "group",
        "signals",
    ]
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    write_atomic(output_dir / "technologies.csv", text.getvalue().encode("utf-8"))

    table = pa.Table.from_pylist(
        rows,
        schema=pa.schema([(c, pa.int32() if c == "confidence" else pa.string()) for c in columns]),
    )
    buffer = pa.BufferOutputStream()
    pq.write_table(table, buffer)
    write_atomic(output_dir / "technologies.parquet", buffer.getvalue().to_pybytes())


def build_summary(lines: list[dict], ruleset: RuleSet, threshold: int, partial: bool) -> dict:
    statuses = Counter(line["status"] for line in lines)
    redirected = [line for line in lines if line["redirected_to"]]
    counted = [line for line in lines if not line["redirected_to"]]  # F4.4/F4.5: left out
    ok_counted = [line for line in counted if line["status"] == "ok"]

    # Name level: a technology's group is "core" if it is core on any counted domain.
    groups_by_name: dict[str, set[str]] = defaultdict(set)
    best_confidence: dict[str, int] = defaultdict(int)
    for line in counted:
        for tech in line["technologies"]:
            if tech["confidence"] < threshold:
                continue
            groups_by_name[tech["name"]].add(tech["group"])
            best_confidence[tech["name"]] = max(best_confidence[tech["name"]], tech["confidence"])

    def name_group(groups: set[str]) -> str:
        for group in GROUPS:  # core first: website evidence anywhere wins
            if group in groups:
                return group
        return "core"

    group_of = {name: name_group(groups) for name, groups in groups_by_name.items()}
    unique = Counter(group_of.values())
    core_names = sorted(n for n, g in group_of.items() if g == "core")

    core_per_ok_domain = [
        len(
            {
                t["name"]
                for t in line["technologies"]
                if t["group"] == "core" and t["confidence"] >= threshold
            }
        )
        for line in ok_counted
    ]
    by_category: Counter[str] = Counter()
    for name in core_names:
        for category in ruleset.technologies[name].categories:
            if category in ruleset.categories:
                by_category[ruleset.categories[category].name] += 1

    not_checked: dict[str, dict[str, int]] = {}
    for line in counted:
        for field_name, count in line.get("not_checked", {}).items():
            entry = not_checked.setdefault(field_name, {"domains": 0, "patterns_max": 0})
            entry["domains"] += 1
            entry["patterns_max"] = max(entry["patterns_max"], count)

    ok_label = f"over {len(ok_counted)} ok domains of {len(lines)}"
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool_version": tool_version(),
        "ruleset_version": ruleset.version,
        "partial_run": partial,
        "confidence_threshold": threshold,
        "domains": {
            "input": len(lines),
            "ok": statuses["ok"],
            "bot_challenge": statuses["bot_challenge"],
            "failed": dict(
                sorted(
                    (s.removeprefix("failed:"), n)
                    for s, n in statuses.items()
                    if s.startswith("failed:")
                )
            ),
            "redirected_elsewhere": {
                "count": len(redirected),
                "note": "left out of every count, average and lead below",
                "list": [f"{line['domain']} -> {line['redirected_to']}" for line in redirected],
            },
        },
        "unique_technologies": {
            "core": unique["core"],
            "separately": {group: unique[group] for group in GROUPS[1:]},
            "extended_total": len(group_of),
            "core_at_confidence_50_or_more": sum(1 for n in core_names if best_confidence[n] >= 50),
            "core_at_confidence_80_or_more": sum(1 for n in core_names if best_confidence[n] >= 80),
        },
        "core_per_domain": {
            "average": round(sum(core_per_ok_domain) / len(core_per_ok_domain), 1)
            if core_per_ok_domain
            else None,
            "domains_with_0_core_technologies": core_per_ok_domain.count(0),
            "denominator": ok_label,
        },
        "core_by_category": dict(by_category.most_common()),
        "not_checked": not_checked,
        "core_technologies": core_names,
    }


def write_outputs(
    lines: list[dict], ruleset: RuleSet, output_dir: Path, threshold: int, partial: bool
) -> Outputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
    write_atomic(output_dir / "detections.jsonl", text.encode("utf-8"))
    _write_tables(_rows(lines, threshold), output_dir)
    summary = build_summary(lines, ruleset, threshold, partial)
    write_json_atomic(output_dir / "summary.json", summary)
    return Outputs(lines, summary)


def check_run_complete(raw_dir: Path, allow_partial: bool) -> bool:
    """True if the folder holds a finished collection; raises unless partial is allowed."""
    if (raw_dir / RUN_COMPLETE).is_file():
        return True
    if not allow_partial:
        raise NotFinishedError(
            f"{raw_dir} has no {RUN_COMPLETE}: the collection did not finish. Re-run "
            "collect, or pass --allow-partial (summary.json will say so)."
        )
    return False


def default_workers() -> int:
    return max(1, (os.cpu_count() or 2) - 1)
