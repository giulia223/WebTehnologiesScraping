"""Signal ablation: what each collection step adds, and what it costs (Task 7.1).

Detection is re-run four times on the same saved data:

    static      the plain HTTP fetch (static.html, static.json), the certificate, robots.txt
    static+     the same files, plus URLs written inside the page's inline scripts (Task 7.3;
                no extra request, so no extra cost; checked against the browser in
                evaluate/static_plus.py)
    + JS files  static+, plus the script files downloaded over plain HTTP (Task 7.4:
                script-content rules, URLs inside Google Tag Manager containers)
    + browser   adds the browser's rendered page, requests, cookies and JS variables
    + DNS       adds the DNS records (= the full system, the same as `techdetect detect`)

Detection is re-run, not filtered from the full output: implies, excludes and requires
depend on what else was found (a technology implied by one found only in the browser must
disappear with it), so they are recomputed for every row.

Every row counts the same domains (status and redirects always come from both fetches);
only the evidence changes. Precision and recall reuse the existing labels; detections
without a verdict are left out and counted. Cost is the average wall time per domain of
the collection steps a row uses, from the collect run log.
"""

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from techdetect.detect import build_summary, counted, detect_all
from techdetect.domains import Domain
from techdetect.evaluate.metrics import compute, rate
from techdetect.evaluate.static_plus import from_js_files, load_review, referee
from techdetect.fingerprints.loader import RuleSet
from techdetect.fingerprints.matcher import INLINE_URLS, JS_FILES

# (label, sources matched, collection steps paid for). Each row adds one thing; static+
# is a branch of static (the browser rows do not read inline URLs: the browser sees those
# scripts load, if they do).
ROWS: tuple[tuple[str, frozenset[str], tuple[str, ...]], ...] = (
    ("static", frozenset({"static", "tls", "robots"}), ("static", "tls", "wellknown")),
    (
        "static+",
        frozenset({"static", "tls", "robots", INLINE_URLS}),
        ("static", "tls", "wellknown"),
    ),
    (
        "static+ JS files",
        frozenset({"static", "tls", "robots", INLINE_URLS, JS_FILES}),
        ("static", "tls", "wellknown", "js"),
    ),
    (
        "+ browser",
        frozenset({"static", "tls", "robots", "browser"}),
        ("static", "tls", "wellknown", "browser"),
    ),
    (
        "+ DNS",
        frozenset({"static", "tls", "robots", "browser", "dns"}),
        ("static", "tls", "wellknown", "browser", "dns"),
    ),
)

JS_REVIEW_FILE = Path("data/js_files_review.json")


def step_costs(run_log: Path) -> dict[str, float]:
    """Average seconds per domain for each collection step, from its latest run.

    The log keeps every run (a step can be re-collected alone), so for each step only the
    records of its most recent run are averaged; failures count, they cost time too."""
    by_run: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for text in run_log.read_text(encoding="utf-8").splitlines():
        if text:
            record = json.loads(text)
            by_run[record["step"]][record["run_started_at"]].append(record["duration_ms"])
    costs = {}
    for step, runs in by_run.items():
        latest = runs[max(runs)]
        costs[step] = sum(latest) / len(latest) / 1000
    return costs


def core_sites(lines: list[dict[str, Any]]) -> dict[str, int]:
    """Core name -> number of counted domains where it is core (as in summary.json)."""
    sites: Counter[str] = Counter()
    for line in lines:
        if counted(line):
            sites.update({t["name"] for t in line["technologies"] if t["group"] == "core"})
    return dict(sites)


def pages_read(lines: list[dict[str, Any]], artifact: str) -> int:
    """Counted ok domains on which this page was matched."""
    return sum(
        1
        for line in lines
        if counted(line)
        and line["status"] == "ok"
        and line.get("artifacts", {}).get(artifact) == "used"
    )


def measure_row(
    label: str,
    lines: list[dict[str, Any]],
    steps: tuple[str, ...],
    ruleset: RuleSet,
    ground_truth: dict[str, Any],
    costs: dict[str, float],
) -> dict[str, Any]:
    summary = build_summary(lines, ruleset, threshold=0, partial=False)
    unique = summary["unique_technologies"]
    metrics = compute(ground_truth, {line["domain"]: line for line in lines})
    headline = metrics["headline"][0]  # threshold 0, as the headline counts
    return {
        "label": label,
        "steps": list(steps),
        "unique_core": unique["core"],
        "unique_extended": unique["extended_total"],
        "dns_only": unique["separately"].get("dns_only", 0),
        "detections": sum(len(line["technologies"]) for line in lines),
        "core_per_domain": summary["core_per_domain"]["average"],
        "core_per_domain_over": summary["core_per_domain"]["denominator"],
        "precision": headline["precision"],
        "unsure": headline["unsure"],
        "judged": metrics["judged"],
        "unlabelled": len(metrics["unlabelled"]),
        "recall": headline["recall"],
        "cost_s_per_domain": round(sum(costs.get(s, 0.0) for s in steps), 2),
        "core_sites": core_sites(lines),
    }


def run_ablation(
    raw_dir: Path,
    domains: list[Domain],
    rules_dir: Path,
    ruleset: RuleSet,
    ground_truth: dict[str, Any],
    workers: int,
) -> dict[str, Any]:
    costs = step_costs(raw_dir / "_run_log.jsonl")
    rows = []
    lines_by_row: dict[str, list[dict[str, Any]]] = {}
    for label, sources, steps in ROWS:
        lines = detect_all(raw_dir, domains, rules_dir, workers=workers, sources=sources)
        lines_by_row[label] = lines
        rows.append(measure_row(label, lines, steps, ruleset, ground_truth, costs))
    full = lines_by_row[ROWS[-1][0]]
    pages = {
        "static.html": pages_read(full, "static.html"),
        "rendered.html": pages_read(full, "rendered.html"),
        "ok_counted": sum(1 for x in full if counted(x) and x["status"] == "ok"),
    }
    static_row, browser_row = rows[0], rows[3]
    static_plus = referee(
        raw_dir,
        lines_by_row["static"],
        lines_by_row["static+"],
        set(browser_row["core_sites"]),
        set(static_row["core_sites"]),
        ruleset,
    )
    js_files = referee(
        raw_dir,
        lines_by_row["static+"],
        lines_by_row["static+ JS files"],
        set(browser_row["core_sites"]),
        set(static_row["core_sites"]),
        ruleset,
        is_new=from_js_files,
        browser_lines=lines_by_row["+ browser"],
    )
    return {
        "rows": rows,
        "step_costs": costs,
        "pages": pages,
        "static_plus": static_plus,
        "js_files": js_files,
    }


def gained(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(set(after["core_sites"]) - set(before["core_sites"]))


def by_category(names: list[str], row: dict[str, Any], ruleset: RuleSet) -> list[tuple]:
    """(category, [(name, sites)]) with each name under its first category, the
    categories with most names first."""
    groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for name in names:
        ids = ruleset.technologies[name].categories
        category = ruleset.categories[ids[0]].name if ids else "(no category)"
        groups[category].append((name, row["core_sites"][name]))
    return sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def check_against_summary(result: dict[str, Any], summary: dict[str, Any]) -> str | None:
    """The last row is the full system: it must equal `techdetect detect`'s summary.json."""
    full = result["rows"][-1]
    unique = summary["unique_technologies"]
    if (full["unique_core"], full["unique_extended"]) != (unique["core"], unique["extended_total"]):
        return (
            f"the full row ({full['unique_core']} core, {full['unique_extended']} extended) "
            f"differs from summary.json ({unique['core']} core, {unique['extended_total']} "
            "extended): re-run `techdetect detect` with the same rules"
        )
    return None


def render(result: dict[str, Any], ruleset: RuleSet, meta: dict[str, Any]) -> str:
    rows = result["rows"]
    static, plus, js_row, browser, full = rows
    pages = result["pages"]
    costs = result["step_costs"]
    gain = browser["unique_core"] - static["unique_core"]
    browser_names = gained(static, browser)
    lost_names = sorted(set(static["core_sites"]) - set(browser["core_sites"]))
    dns_names = gained(browser, full)
    out = [
        "# Ablation: what each collection step adds",
        "",
        "Generated by `techdetect evaluate ablation` from the saved raw data (`data/raw/`), "
        "the rules and `data/ground_truth.json`. Do not edit by hand.",
        "",
        f"- **Run:** tool `{meta.get('tool_version', '?')}`, rules "
        f"`{meta.get('ruleset_version', '?')}`, confidence threshold 0.",
        "- **How:** detection is re-run once per row, each time allowing one more collection "
        "step. It is re-run, not filtered: a technology implied by one found only in the "
        "browser disappears with it. **static+** is the static row plus the URLs written "
        "inside the page's inline scripts (no extra request, so no extra cost); the browser "
        "rows do not read inline URLs.",
        "- **Same domains in every row:** a domain's status and redirect always come from "
        "both fetches, so only the evidence differs. A static-only tool would also lose the "
        f"pages only the browser could read: here the static fetch read "
        f"{pages.get('static.html', '?')} and the browser {pages.get('rendered.html', '?')} "
        f"of the {pages.get('ok_counted', '?')} counted working sites.",
        "- **Precision and recall** reuse the existing labels (20 labelled domains). "
        "Detections without a verdict (possible when a smaller set of evidence changes what "
        "is excluded or implied) are left out and counted in the table.",
        "- **Cost** is the average wall time per domain of the steps a row uses, from the "
        "collect run log (latest run of each step). CPU and memory per browser, the real "
        "cost at scale, were not measured.",
        "",
        "## Cumulative table",
        "",
        "| Row | Steps | Unique core | Unique extended | DNS-only | Detections | Core per "
        "domain | Precision | Unsure | Unlabelled | Recall | Cost (s/domain) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        out.append(
            f"| {r['label']} | {', '.join(r['steps'])} | {r['unique_core']} "
            f"| {r['unique_extended']} | {r['dns_only']} | {r['detections']} "
            f"| {r['core_per_domain']} | {rate(*r['precision'])} | {r['unsure']} "
            f"| {r['unlabelled']} | {rate(*r['recall'])} | {r['cost_s_per_domain']} |"
        )
    out += [
        "",
        f"Core per domain is {full['core_per_domain_over']}. Step costs (s/domain): "
        + ", ".join(f"{s} {costs[s]:.2f}" for s in sorted(costs))
        + ".",
        "",
        "## What the browser buys",
        "",
        f"Adding the browser raises unique core from {static['unique_core']} to "
        f"{browser['unique_core']} ({gain:+d}, {gain / static['unique_core']:+.0%} over the "
        f"static row), for {browser['cost_s_per_domain'] - static['cost_s_per_domain']:.2f} "
        f"more seconds per domain ({browser['cost_s_per_domain'] / static['cost_s_per_domain']:.1f}"
        "x the static row's cost).",
        "",
        f"{len(browser_names)} names are core only once the browser is added "
        "(sites = counted domains where the name is core in the + browser row), "
        "each under its first category:",
        "",
        "| Category | Names (sites) |",
        "|---|---|",
    ]
    for category, names in by_category(browser_names, browser, ruleset):
        listed = ", ".join(f"{n} ({s})" for n, s in sorted(names))
        out.append(f"| {category} ({len(names)}) | {listed} |")
    out += ["", "Names core in the static row but not after adding the browser: "]
    out[-1] += (", ".join(lost_names) + ".") if lost_names else "none."
    out += [
        "",
        "## What DNS adds",
        "",
        f"Unique core {browser['unique_core']} -> {full['unique_core']}, DNS-only group "
        f"{browser['dns_only']} -> {full['dns_only']}: DNS records mostly feed the separate "
        "DNS-only group (mail provider, name servers, verification tokens), not core. "
        "Names that become core only with DNS: "
        + ((", ".join(dns_names) + ".") if dns_names else "none."),
    ]
    out += render_static_plus(result["static_plus"], static, plus, load_review())
    out += render_js_files(result["js_files"], plus, js_row, load_review(JS_REVIEW_FILE))
    return "\n".join(out) + "\n"


def render_static_plus(
    sp: dict[str, Any],
    static: dict[str, Any],
    plus: dict[str, Any],
    review: dict[tuple[str, str], dict[str, str]],
) -> list[str]:
    items = sp["additions"]
    by_browser = Counter(item["browser"] for item in items)
    unconfirmed = [i for i in items if i["browser"] == "not_confirmed"]
    verdicts = Counter(
        review[(i["domain"], i["name"])]["verdict"]
        for i in unconfirmed
        if (i["domain"], i["name"]) in review
    )
    judged = verdicts["correct"] + verdicts["wrong"]
    recovered = sp["recovered_names"]
    if review:
        reviewed = (
            f"{verdicts['correct']} correct, {verdicts['wrong']} wrong, "
            f"{verdicts['unsure']} unsure; precision {rate(verdicts['correct'], judged)}; "
            f"not judged yet {len(unconfirmed) - sum(verdicts.values())}."
        )
    else:
        reviewed = "not judged yet."
    not_in_browser = sp["not_in_browser_row"]
    out = [
        "",
        "## Static+: URLs inside inline scripts, without a browser",
        "",
        "Many trackers are installed by a small inline loader snippet that contains their "
        "script URL. The static+ row matches script-URL rules (scriptSrc on the URL, xhr on "
        "its host) against those URLs in `static.html`; such detections have tier "
        "`declared` (the site's code names the script, nothing was seen running).",
        "",
        f"- **Unique core:** {static['unique_core']} -> {plus['unique_core']} "
        f"({plus['unique_core'] - static['unique_core']:+d}) at no extra cost.",
        f"- **Browser-only names recovered:** {len(recovered)} of the "
        f"{sp['browser_only_names']} names the browser adds over the static row"
        + (": " + ", ".join(recovered) + "." if recovered else "."),
        f"- **Additions** (a technology core on a site in static+ but not in static): "
        f"{len(items)}. The browser is the referee: for each, did the browser request a URL "
        "the same rule matches?",
        f"  - confirmed (the script really loaded): {by_browser['confirmed']}",
        "  - not confirmed (the browser got the page but never requested it): "
        f"{by_browser['not_confirmed']}",
        f"  - no browser page to check against: {by_browser['no_browser_page']}",
        "- **Not confirmed, judged by a separate reviewer** (docs/DEFINITIONS.md): " + reviewed,
        "- **Names static+ claims that the browser rows never have as core:** "
        + (", ".join(not_in_browser) + "." if not_in_browser else "none."),
        "",
        "### Every addition",
        "",
        "| Domain | Technology | Browser | Review | Inline URL | Context |",
        "|---|---|---|---|---|---|",
    ]
    for i in sorted(items, key=lambda i: (i["browser"], i["name"], i["domain"])):
        e = i["evidence"][0] if i["evidence"] else {}
        verdict = review.get((i["domain"], i["name"]))
        shown = f"{verdict['verdict']}: {_cell(verdict['reason'], 160)}" if verdict else "-"
        out.append(
            f"| {i['domain']} | {i['name']} | {i['browser']} | {shown} "
            f"| {_cell(e.get('location') or '', 90)} | {_cell(e.get('context') or '', 120)} |"
        )
    return out


def _cell(text: str, limit: int) -> str:
    text = str(text).replace("|", "\\|").replace("`", "'").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def render_js_files(
    sp: dict[str, Any],
    plus: dict[str, Any],
    js_row: dict[str, Any],
    review: dict[tuple[str, str], dict[str, str]],
) -> list[str]:
    """The section for the script files downloaded over plain HTTP (Task 7.4)."""
    items = sp["additions"]
    by_browser = Counter(item["browser"] for item in items)
    unconfirmed = [i for i in items if i["browser"] == "not_confirmed"]
    verdicts = Counter(
        review[(i["domain"], i["name"])]["verdict"]
        for i in unconfirmed
        if (i["domain"], i["name"]) in review
    )
    judged = verdicts["correct"] + verdicts["wrong"]
    if review:
        reviewed = (
            f"{verdicts['correct']} correct, {verdicts['wrong']} wrong, "
            f"{verdicts['unsure']} unsure; precision {rate(verdicts['correct'], judged)}; "
            f"not judged yet {len(unconfirmed) - sum(verdicts.values())}."
        )
    else:
        reviewed = "not judged yet."
    recovered = sp["recovered_names"]
    not_in_browser = sp["not_in_browser_row"]
    extra = js_row["cost_s_per_domain"] - plus["cost_s_per_domain"]
    out = [
        "",
        "## JS files: scripts downloaded over plain HTTP, without a browser",
        "",
        "The collect step `js` downloads, as plain files, every `<script src>` of the static "
        "page and the Google Tag Manager container of every `GTM-` id in it; nothing is run. "
        "Detection then matches the 828 rules that read script contents (a vendor's API host "
        "or a library banner inside its code), and the script-URL rules against URLs inside "
        "the GTM containers (tier `declared`, as for inline URLs).",
        "",
        f"- **Unique core:** {plus['unique_core']} (static+) -> {js_row['unique_core']} "
        f"({js_row['unique_core'] - plus['unique_core']:+d}) for {extra:.2f} more seconds "
        "per domain.",
        f"- **Browser-only names recovered** (with static+): {len(recovered)} of the "
        f"{sp['browser_only_names']} names the browser adds over the static row"
        + (": " + ", ".join(recovered) + "." if recovered else "."),
        f"- **Additions** (core on a site with JS files but not in static+): {len(items)}. "
        "The browser is the referee: did the browser run find the same technology on the "
        "same site by its own evidence? (That the file loaded proves nothing: text inside a "
        "bundle is often only a mention.)",
        f"  - confirmed (the browser run found it too): {by_browser['confirmed']}",
        f"  - not confirmed (the browser got the page, but found no such technology): "
        f"{by_browser['not_confirmed']}",
        f"  - no browser page to check against: {by_browser['no_browser_page']}",
        "- **Not confirmed, judged by a separate reviewer** (docs/DEFINITIONS.md): " + reviewed,
        "- **Names the JS files claim that the browser rows never have as core:** "
        + (", ".join(not_in_browser) + "." if not_in_browser else "none."),
        "",
        "### Every addition",
        "",
        "| Domain | Technology | Browser | Review | Evidence | File or URL |",
        "|---|---|---|---|---|---|",
    ]
    for i in sorted(items, key=lambda i: (i["browser"], i["name"], i["domain"])):
        e = i["evidence"][0] if i["evidence"] else {}
        verdict = review.get((i["domain"], i["name"]))
        shown = f"{verdict['verdict']}: {_cell(verdict['reason'], 160)}" if verdict else "-"
        what = f"{e.get('signal', '?')} `{_cell(e.get('matched_value') or '', 60)}`"
        out.append(
            f"| {i['domain']} | {i['name']} | {i['browser']} | {shown} | {what} "
            f"| {_cell(e.get('location') or '', 90)} |"
        )
    return out
