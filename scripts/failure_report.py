"""Failure report: every domain the tool could not read cleanly, grouped by cause.

Usage:  python scripts/failure_report.py [--include-blind] [--out docs/FAILURES.md]

Reads data/output/detections.jsonl and data/raw/ (offline). A domain is in the report if
it failed, hit a bot wall, redirected to another site, or was read but gave very few
technologies. For each: status, final URL, page title, HTML size, screenshot path.

The blind ground-truth domains are left out unless --include-blind is given: their
results must not be seen before their labels are written.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

RAW = Path("data/raw")
DETECTIONS = Path("data/output/detections.jsonl")
GROUND_TRUTH = Path("data/ground_truth.json")
# Notes from checking the failures by hand: kept in their own file, appended to the report,
# so regenerating the report never overwrites them.
CHECKED_BY_HAND = Path("docs/failures_checked.md")
FEW_TECHNOLOGIES = 2  # an ok domain with at most this many core technologies is listed

# Order = how a domain is classified (first match wins), and the order of the report.
GROUPS = {
    "dead": "Dead: the name does not resolve",
    "dead_behind_cdn": "Dead behind a CDN: the CDN answers, but the site behind it is gone",
    "bot_challenge": "Bot wall recognised (challenge page)",
    "blocked_403": "Blocked with 403, blocker not recognisable (left as an HTTP error)",
    "site_broken": "Site broken, closed or not connected (404, 5xx, platform error page)",
    "timeout": "No answer in time",
    "parked_lander": "Parked: ends on an ad / parking lander page",
    "redirected": "Redirected to another site (left out of the counts)",
    "placeholder": "Placeholder page: hosting default, coming soon, device default",
    "few_technologies": f"Read fine, but at most {FEW_TECHNOLOGIES} core technologies",
}
# What a person can do about each group, for the summary table.
FIXABLE = {
    "dead": "no: nothing to read",
    "dead_behind_cdn": "no: nothing to read (now reported as dns_error)",
    "bot_challenge": "no: we do not bypass protections (block pages now recognised too)",
    "blocked_403": "no: a plain 403 does not say who blocked us",
    "site_broken": "no: the site itself is broken",
    "timeout": "maybe: a longer timeout or a retry another day",
    "parked_lander": "no: no website; status `parked`, left out of the counts",
    "redirected": "rule choice: credit the target or not",
    "placeholder": "no: no website yet; status `placeholder`, left out of the counts",
    "few_technologies": "check by hand: minimal site or a gap in the rules",
}
PARKING = re.compile(
    rb"domain (?:name )?(?:is|may be) for sale|buy this domain|\bsedo(?:parking)?\.com\b"
    rb"|\bdan\.com\b|\bafternic\b|\bparkingcrew\b|\bbodis\.com\b|parked (?:free|by|domain)"
    rb"|domain parking",
    re.I,
)
TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def page_facts(host: str) -> dict:
    """Title, HTML sizes, screenshot and a parking check from the saved pages."""
    folder = RAW / host
    static, browser = read_json(folder / "static.json"), read_json(folder / "browser.json")
    sizes, title, parked = {}, "", None
    for name in ("rendered.html", "static.html"):
        path = folder / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        sizes[name] = len(data)
        if not title and (match := TITLE.search(data[:300_000])):
            title = " ".join(match.group(1).decode("utf-8", "replace").split())[:80]
        if parked is None and (hit := PARKING.search(data[:400_000])):
            parked = hit.group(0).decode("utf-8", "replace")
    screenshot = folder / "screenshot.jpg"
    return {
        "static": static.get("error_category") or ("ok" if static else "-"),
        "static_http": static.get("http_status"),
        "browser": browser.get("error_category") or ("ok" if browser else "-"),
        "browser_http": browser.get("http_status"),
        "final_url": browser.get("final_url") or static.get("final_url") or "",
        "title": title,
        "sizes": sizes,
        "screenshot": str(screenshot).replace("\\", "/") if screenshot.is_file() else "",
        "parked": parked,
    }


def classify(line: dict, facts: dict) -> str | None:
    status = line["status"]
    core = sum(1 for t in line["technologies"] if t["group"] == "core")
    # Statuses come from `detect`, which holds the rules; this script only groups them.
    if status == "failed:dns_error":
        # Cloudflare error 1001 ("DNS resolution error"): the CDN answers, the site is gone.
        return "dead_behind_cdn" if "dns resolution error" in facts["title"].lower() else "dead"
    if status == "bot_challenge":
        return "bot_challenge"
    if status == "failed:http_error" and 403 in (facts["static_http"], facts["browser_http"]):
        return "blocked_403"
    if status.startswith("failed:http_error"):
        return "site_broken"
    if status == "failed:timeout":
        return "timeout"
    if status.startswith("failed:"):
        return "site_broken"
    if status == "parked":
        return "parked_lander"
    if status == "placeholder":
        return "placeholder"
    if line["redirected_to"]:
        return "redirected"
    if core <= FEW_TECHNOLOGIES:
        return "few_technologies"
    return None


def build(include_blind: bool) -> tuple[dict[str, list], int, int]:
    blind = set(read_json(GROUND_TRUTH).get("sets", {}).get("blind", []))
    lines = [json.loads(x) for x in DETECTIONS.read_text(encoding="utf-8").splitlines() if x]
    groups: dict[str, list] = defaultdict(list)
    hidden = 0
    for line in lines:
        if line["domain"] in blind and not include_blind:
            hidden += 1  # checked first: nothing about a blind domain is read or shown
            continue
        facts = page_facts(line["domain"])
        group = classify(line, facts)
        if group:
            groups[group].append({"line": line, **facts})
    return groups, hidden, len(lines)


def render(groups: dict[str, list], hidden: int, total: int) -> str:
    out = [
        "# Failures",
        "",
        "Every domain the tool could not read cleanly, grouped by cause (generated by "
        "`scripts/failure_report.py` from `data/output/detections.jsonl` and `data/raw/`).",
        "",
        f"Domains: {total}. In this report: {sum(len(v) for v in groups.values())}."
        + (
            f" Left out: {hidden} blind ground-truth domains (until their labels exist)."
            if hidden
            else ""
        ),
        "",
        "| Group | Domains | Fixable? |",
        "|---|---|---|",
    ]
    out += [f"| {GROUPS[g]} | {len(groups.get(g, []))} | {FIXABLE[g]} |" for g in GROUPS]
    for group, title in GROUPS.items():
        members = groups.get(group, [])
        if not members:
            continue
        header = (
            "| Domain | Status | Static / browser | Final URL | Title | HTML bytes | Screenshot |"
        )
        out += ["", f"## {title} ({len(members)})", "", header, "|---|---|---|---|---|---|---|"]
        for m in members:
            line = m["line"]
            static = f"{m['static']} {m['static_http'] or ''}".strip()
            browser = f"{m['browser']} {m['browser_http'] or ''}".strip()
            target = f" -> {line['redirected_to']}" if line["redirected_to"] else ""
            size = max(m["sizes"].values()) if m["sizes"] else 0
            title_text = m["title"].replace("|", "/")
            out.append(
                f"| {line['domain']}{target} | {line['status']} | {static} / {browser} "
                f"| {m['final_url'][:70]} | {title_text} | {size} | {m['screenshot']} |"
            )
    parked = [m for ms in groups.values() for m in ms if m["parked"]]
    out += ["", "## Parked / for-sale pages (a finding, not a failure)", ""]
    out += [f"- {m['line']['domain']}: '{m['parked']}'" for m in parked] or [
        "- none found by the keyword check (domain for sale, Sedo, Dan.com, Afternic, "
        "ParkingCrew, Bodis, domain parking)"
    ]
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--include-blind", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("docs/FAILURES.md"))
    args = parser.parse_args()
    groups, hidden, total = build(args.include_blind)
    text = render(groups, hidden, total)
    if CHECKED_BY_HAND.is_file():
        text += "\n" + CHECKED_BY_HAND.read_text(encoding="utf-8")
    args.out.write_text(text, encoding="utf-8")
    sys.stdout.write(
        f"wrote {args.out}: "
        + ", ".join(f"{g} {len(groups.get(g, []))}" for g in GROUPS)
        + f"; blind left out: {hidden}\n"
    )


if __name__ == "__main__":
    main()
