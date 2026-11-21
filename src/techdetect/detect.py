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
import re
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
from selectolax.lexbor import LexborHTMLParser

from techdetect.collect.challenge import detect_challenge, is_dead_behind_cdn
from techdetect.collect.provenance import tool_version
from techdetect.collect.run import RUN_COMPLETE
from techdetect.collect.storage import write_atomic, write_json_atomic
from techdetect.domains import Domain, domain_name, registered_domain
from techdetect.fingerprints.combine import PatternGroup, TechnologyResult, combine
from techdetect.fingerprints.loader import RuleSet, load_rules
from techdetect.fingerprints.matcher import SOURCES, RuleIndex, match_domain
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
GROUPS = ("core", "dns_only", "inferred_only", "themes", "platform_infra")
# Hosted website builders: the platform, not the site's owner, chose the servers, CDN,
# storage, bot protection and certificate behind every site it hosts. On such a site those
# detections are true but belong to the platform (docs/DEFINITIONS.md), so they move to the
# group "platform_infra": reported, never counted as the site's technologies.
HOSTED_PLATFORMS = frozenset(
    {
        "Wix",
        "Weebly",
        "MyWebsite",  # IONOS MyWebsite
        "Squarespace",
        "Shopify",
        "Webflow",
        "GoDaddy Website Builder",
        "Duda",
        "WebNode",
        "Square Online",
        # Added after the name check (Task 4.5): a restaurant-site builder (sites live on
        # *.eatbu.com) and a musicians' site builder (Bandzoogle: a reviewed custom rule).
        "DISH",
        "Bandzoogle",
    }
)
INFRASTRUCTURE_CATEGORIES = frozenset(
    {
        "CDN",
        "PaaS",
        "IaaS",
        "Hosting",
        "Web servers",
        "Caching",
        "SSL/TLS certificate authorities",
        "Reverse proxies",
        "Load balancers",
    }
)
# Bot protection sits in "Security" with HSTS and other things a page itself carries, so
# it is named one by one.
BOT_PROTECTION = frozenset(
    {"Cloudflare Bot Management", "Akamai Bot Manager", "DataDome", "PerimeterX"}
)
# The platform's own monitoring and tracking, reporting to the platform, not to the owner:
# Wix's Sentry sends to sentry.wixpress.com, Weebly's Snowplow to ec.editmysite.com.
# Found by the name check (Task 4.5), each seen on every site of that platform here:
# - Wix: Firebase from the Wix Chat frame (wix-engage-visitors-*.firebaseio.com, 4 sites);
# - Square Online: Datadog reporting as service "spi-ecom-square-online-buyer-journey";
# - DISH: the template's Adobe stack (data layer productName "DISH WEBSITE B2C");
# - Bandzoogle: Ruby on Rails from its `csrf-param` meta (the builder's server framework;
#   its servers OpenResty and Varnish move anyway, by category).
# Trade-off: an owner who adds the same tool themselves on that platform is moved too.
PLATFORM_OWN_TOOLS = {
    "Wix": frozenset({"Sentry", "Firebase"}),
    "Weebly": frozenset({"Snowplow Analytics"}),
    "Square Online": frozenset({"Datadog"}),
    "DISH": frozenset(
        {
            "Adobe Analytics",
            "Adobe Client Data Layer",
            "Adobe Experience Platform Identity Service",
            "Adobe Experience Platform Launch",
        }
    ),
    "Bandzoogle": frozenset({"Ruby on Rails"}),
}
# Statuses of domains whose page is not the owner's website: left out of all counts.
NOT_THE_SITE = frozenset({"parked", "placeholder"})

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


# Pages that are not the site's own website, built from the real examples in the data:
# - parked: the page ends on an ad / parking "lander" (".../lander", "lander_v4.php",
#   "middle.html?impId=...");
# - placeholder: the whole title is a hosting company's or a device's default page, or a
#   "coming soon" page ("Default page", "Site Hospedado na KingHost",
#   "Hello! Welcome to Synology Web Station!", "Coming Soon", "無効なURLです" = invalid URL).
_LANDER_URL = re.compile(r"/lander\b|lander_v\d|[?&]impid=", re.I)
_PLACEHOLDER_TITLE = re.compile(
    r"^\s*(?:default page|coming soon|under construction)\s*[!.]?\s*$"
    r"|^\s*site hospedado na\b"
    r"|welcome to synology web station"
    r"|^\s*無効なurlです\s*$",
    re.I,
)


# A domain-sale page (seen on weissbauchpapageien.de: "... steht zum Verkauf",
# "This domain is for sale", "Buy this domain"). Phrases about the domain itself only, so
# a shop's "for sale" or "zu verkaufen" items never match.
_FOR_SALE_TEXT = re.compile(rb"this domain is for sale|buy this domain", re.I)
_FOR_SALE_TITLE = re.compile(r"(?:is for sale|steht zum verkauf)", re.I)


def says_for_sale(folder: Path, page: dict | None, title: str, host: str) -> bool:
    """The page offers the domain itself for sale."""
    if not page or page.get("status") != "ok":
        return False
    if host.lower() in title.lower() and _FOR_SALE_TITLE.search(title):
        return True
    path = folder / (page.get("html_file") or "")
    return path.is_file() and bool(_FOR_SALE_TEXT.search(path.read_bytes()[:400_000]))


def page_title(folder: Path, page: dict | None) -> str:
    """The <title> of a saved page, "" if there is none."""
    if not page or not page.get("html_file") or not (folder / page["html_file"]).is_file():
        return ""
    title = LexborHTMLParser((folder / page["html_file"]).read_bytes()).css_first("title")
    return " ".join(title.text().split()) if title else ""


def refine_failure(folder: Path, page: dict | None) -> dict | None:
    """Look again at a failed page with today's rules (offline, from the saved files).

    The collector's challenge check missed some block pages (Akamai deny pages without an
    AkamaiGHost header, Cloudflare's "you have been blocked" page), and Cloudflare's
    "DNS resolution error" page is a dead site, not an HTTP error. Re-reading the saved
    page fixes the status without a new crawl.
    """
    if not page or page.get("status") == "ok" or not page.get("html_file"):
        return page
    path = folder / page["html_file"]
    if not path.is_file():
        return page
    body = path.read_bytes()
    status = page.get("http_status") or 0
    headers = [(k, v) for k, v in page.get("main_document_headers", [])]
    if "attempts" in page:  # static.json: the chosen attempt's final response
        steps = page["attempts"][page["chosen_attempt"] - 1].get("steps", [])
        headers = [(k, v) for k, v in steps[-1]["headers"]] if steps else []
    if is_dead_behind_cdn(status, body):
        return {**page, "error_category": "dns_fail"}
    vendor = detect_challenge(status, headers, body)
    if vendor and page.get("error_category") != "bot_challenge":
        return {**page, "error_category": "bot_challenge", "challenge": vendor}
    return page


def not_a_website(page: dict | None, title: str) -> str | None:
    """ "parked" or "placeholder" for a page that loaded but is not the site's own."""
    if not page or page.get("status") != "ok":
        return None
    if _LANDER_URL.search(page.get("final_url") or ""):
        return "parked"
    if _PLACEHOLDER_TITLE.search(title):
        return "placeholder"
    return None


def domain_status(
    static: dict | None,
    browser: dict | None,
    titles: tuple[str, str] = ("", ""),
    for_sale: bool = False,
) -> str:
    """ok / parked / placeholder / bot_challenge / failed:<category>.

    `titles` are the static and rendered pages' titles, for the placeholder check;
    `for_sale` says that a loaded page offers the domain itself for sale.
    """
    pages = [p for p in (static, browser) if p is not None]
    if not pages:
        return "failed:not_collected"
    if any(p.get("status") == "ok" for p in pages):
        if for_sale:
            return "parked"
        # A loaded page can still be a parking lander or a default page: the browser's
        # view first (it follows JavaScript redirects), then the plain fetch.
        for page, title in ((browser, titles[1]), (static, titles[0])):
            if page and page.get("status") == "ok":
                return not_a_website(page, title) or "ok"
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


SAME_NAME_MIN_CHARS = 5  # a shorter name inside a longer one proves nothing ("abc" in "abcd...")


def same_name(host: str, target: str) -> bool:
    """Are the input domain and its redirect target the same name, i.e. the same company?

    Built from the real redirects in the data: the names (without their ending) are equal
    once hyphens and dots are removed (seringa-safaris.com -> seringasafaris.com), or one
    contains the other (velvety.eu -> velvety-cosmetics.com). A renamed company
    (fsbwyoming.com -> myfrbank.com) or a parent company (allegrocreditbeta.com ->
    synchrony.com) does not match: the safe direction, since crediting a giant parent's
    website to a small brand would mislead a salesperson.
    """
    a, b = (re.sub(r"[^a-z0-9]", "", domain_name(x)) for x in (host, target))
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= SAME_NAME_MIN_CHARS and shorter in longer


def counted(line: dict[str, Any]) -> bool:
    """Same rule as the headline counts in summary.json: the domain's own site (no redirect
    to another company) and a real website (not a parking or placeholder page)."""
    credited = not line["redirected_to"] or bool(line.get("redirect_same_name"))
    return credited and line["status"] not in NOT_THE_SITE


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


def hosted_platform(technologies: list[dict[str, Any]]) -> str | None:
    """The hosted builder the site runs on, if one is seen on the site itself (core)."""
    for tech in technologies:
        if tech["name"] in HOSTED_PLATFORMS and tech["group"] == "core":
            return tech["name"]
    return None


def is_platform_infrastructure(tech: dict[str, Any], platform: str) -> bool:
    return (
        bool(INFRASTRUCTURE_CATEGORIES & set(tech["categories"]))
        or tech["name"] in BOT_PROTECTION
        or tech["name"] in PLATFORM_OWN_TOOLS.get(platform, ())
    )


def move_platform_infrastructure(technologies: list[dict[str, Any]]) -> str | None:
    """On a hosted builder's site, move the platform's infrastructure out of core.

    Only core detections move: DNS records (the domain's mail, its name servers) are the
    owner's choice even on a builder, and page code the platform bundles still runs on the
    site's page. Returns the platform, or None.
    """
    platform = hosted_platform(technologies)
    if platform is None:
        return None
    for tech in technologies:
        if tech["group"] == "core" and is_platform_infrastructure(tech, platform):
            tech["group"] = "platform_infra"
            tech["platform"] = platform
    return platform


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


def detect_domain(
    raw_dir: Path,
    domain: Domain,
    ruleset: RuleSet,
    index: RuleIndex,
    sources: frozenset[str] = SOURCES,
) -> dict:
    """One line of detections.jsonl: the domain's status and every detection with proof.

    `sources` limits which collection steps are matched (the ablation). The status and the
    redirect are always read from both fetches, so every ablation row counts the same
    domains and only the evidence differs."""
    folder = raw_dir / domain.host
    static = refine_failure(folder, _read_json(folder / "static.json"))
    browser = refine_failure(folder, _read_json(folder / "browser.json"))
    titles = (page_title(folder, static), page_title(folder, browser))
    for_sale = says_for_sale(folder, static, titles[0], domain.host) or says_for_sale(
        folder, browser, titles[1], domain.host
    )
    status = domain_status(static, browser, titles, for_sale)
    line: dict[str, Any] = {
        "domain": domain.host,
        "row": domain.row,
        "display_name": domain.display_name,
        "status": status,
        "redirected_to": (target := redirected_to(domain.host, static, browser)),
        # The target is the same company (same name): its technologies count for this domain.
        "redirect_same_name": bool(target and same_name(domain.host, target)),
        "technologies": [],
    }
    if status == "failed:not_collected":
        return line
    matches = match_domain(raw_dir, domain.host, index, sources)
    resolution = resolve(combine(matches.evidence), ruleset)
    line["technologies"] = [
        _technology_json(result, ruleset) for result in resolution.technologies.values()
    ]
    line["hosted_platform"] = move_platform_infrastructure(line["technologies"])
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


def _work(args: tuple[str, Domain, frozenset[str]]) -> dict:
    assert _worker_rules is not None
    raw_dir, domain, sources = args
    return detect_domain(Path(raw_dir), domain, *_worker_rules, sources)


def detect_all(
    raw_dir: Path,
    domains: list[Domain],
    rules_dir: Path,
    include_candidates: bool = False,
    workers: int = 1,
    progress: Callable[[dict], None] | None = None,
    sources: frozenset[str] = SOURCES,
) -> list[dict]:
    """Every domain's line, in input order. Domains are independent, so with workers > 1
    they are matched in parallel processes (matching is CPU work, about 1 s per domain)."""
    jobs = [(str(raw_dir), d, sources) for d in domains]
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

    def credited(line: dict) -> bool:
        # No redirect, or a redirect to the same company's other domain.
        return not line["redirected_to"] or bool(line.get("redirect_same_name"))

    redirected = [
        line for line in lines if not credited(line) and line["status"] not in NOT_THE_SITE
    ]
    same_company = [
        line
        for line in lines
        if line["redirected_to"] and credited(line) and line["status"] not in NOT_THE_SITE
    ]
    # Left out of every count: another site's page (redirect, F4.4/F4.5), and pages that
    # are not a website at all (parking lander, hosting default page). Their detections
    # describe the parking service or the host, not the domain's owner.
    counted = [line for line in lines if credited(line) and line["status"] not in NOT_THE_SITE]
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
            "parked": {
                "count": statuses["parked"],
                "note": "ends on a parking / ad lander; left out of every count below",
                "list": [line["domain"] for line in lines if line["status"] == "parked"],
            },
            "placeholder": {
                "count": statuses["placeholder"],
                "note": 'a hosting or device default page, or "coming soon"; left out of '
                "every count below",
                "list": [line["domain"] for line in lines if line["status"] == "placeholder"],
            },
            "failed": dict(
                sorted(
                    (s.removeprefix("failed:"), n)
                    for s, n in statuses.items()
                    if s.startswith("failed:")
                )
            ),
            "redirected_elsewhere": {
                "count": len(redirected),
                "note": "to another company's site: left out of every count, average "
                "and lead below",
                "list": [f"{line['domain']} -> {line['redirected_to']}" for line in redirected],
            },
            "redirected_same_name": {
                "count": len(same_company),
                "note": "to the same company's other domain (same name): counted for the input",
                "list": [f"{line['domain']} -> {line['redirected_to']}" for line in same_company],
            },
        },
        "unique_technologies": {
            "core": unique["core"],
            "separately": {group: unique[group] for group in GROUPS[1:]},
            # The site's technologies in the wider sense: a platform's infrastructure is not.
            "extended_total": sum(1 for g in group_of.values() if g != "platform_infra"),
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
    lines: list[dict],
    ruleset: RuleSet,
    output_dir: Path,
    threshold: int,
    partial: bool,
    inline_urls: bool = False,
    js_files: bool = False,
) -> Outputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
    write_atomic(output_dir / "detections.jsonl", text.encode("utf-8"))
    _write_tables(_rows(lines, threshold), output_dir)
    summary = build_summary(lines, ruleset, threshold, partial)
    # Off by default; when on, the counts include URLs read from inline scripts (Task 7.3).
    summary["inline_urls"] = inline_urls
    summary["js_files"] = js_files  # likewise for downloaded script files (Task 7.4)
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
