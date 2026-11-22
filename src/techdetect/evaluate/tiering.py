"""A browser only where it pays: a tiered pipeline, simulated on saved data.

At scale the browser is the expensive step. Instead of rendering every domain, run the
cheap tier everywhere and send a domain to the browser only when the cheap tier's own
result says it is likely worth it. The decision uses only cheap-tier facts (no browser
data), so it could run in production exactly as here.

    cheap tier   static fetch, certificate, robots.txt, DNS and inline-script URLs (no
                 browser). The downloaded script files are left out on purpose:
                 most of what they add is a mention inside a bundle (docs/ABLATION.md), and
                 each approach should be judged on its own.
    policy       send a domain to the browser if the cheap tier found
                   - a shop: a technology in category Ecommerce (the client's market), or
                   - a tag manager: it injects trackers that only a running page shows, or
                   - no real page, for a reason a browser can get past: a bot check
                     (aimed at plain HTTP clients), or a TLS or connection error. Not when
                     the site itself is down (no DNS, 5xx, timeout, 4xx). On these 200
                     domains the browser got a page after 10 of 20 such failures and after
                     1 of 29 others; the rule was read off this same data, so it is tuned
                     to it.
    tiered       the cheap result for every domain, replaced by the full result (cheap
                 tier + browser) for the domains the policy sends

Compared with "cheap tier only" and "browser for every domain": unique technologies,
precision and recall from the existing labels, and the cost per domain.
"""

from pathlib import Path
from typing import Any

from techdetect.detect import DEFAULT_MIN_CONFIDENCE, detect_all, is_core
from techdetect.domains import Domain
from techdetect.evaluate.ablation import measure_row, step_costs
from techdetect.evaluate.metrics import rate
from techdetect.fingerprints.loader import RuleSet
from techdetect.fingerprints.matcher import INLINE_URLS, SOURCES

CHEAP_SOURCES = frozenset({"static", "tls", "robots", "dns", INLINE_URLS})
FULL_SOURCES = SOURCES | {INLINE_URLS}
CHEAP_STEPS = ("static", "tls", "wellknown", "dns")
BROWSER_STEP = "browser"
SHOP_CATEGORY = "Ecommerce"
TAG_MANAGER_CATEGORY = "Tag managers"
# Why the plain fetch got no page, as written in the cheap line's artifacts; only these
# can be specific to a plain HTTP client (a browser may still get the page).
BROWSER_MAY_HELP = frozenset({"bot_challenge", "tls_error", "conn_error", "conn_refused"})


def browser_reason(cheap_line: dict[str, Any]) -> str | None:
    """Why this domain should get the browser, from its cheap-tier result; None = it
    should not. The first matching reason is returned."""
    static_page = cheap_line.get("artifacts", {}).get("static.html", "")
    if static_page != "used":
        why = static_page.removeprefix("not used: ")
        return f"plain fetch blocked ({why})" if why in BROWSER_MAY_HELP else None
    core = [t for t in cheap_line["technologies"] if is_core(t)]
    if any(SHOP_CATEGORY in t["categories"] for t in core):
        return "shop"
    if any(TAG_MANAGER_CATEGORY in t["categories"] for t in core):
        return "tag manager"
    return None


def tiered_lines(
    cheap: list[dict[str, Any]], full: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The tiered result: full lines where the policy sends the browser, cheap elsewhere.
    Also returns domain -> reason for the domains sent."""
    full_by_domain = {line["domain"]: line for line in full}
    lines, sent = [], {}
    for line in cheap:
        reason = browser_reason(line)
        if reason:
            sent[line["domain"]] = reason
            lines.append(full_by_domain[line["domain"]])
        else:
            lines.append(line)
    return lines, sent


def run_tiering(
    raw_dir: Path,
    domains: list[Domain],
    rules_dir: Path,
    ruleset: RuleSet,
    ground_truth: dict[str, Any],
    workers: int,
) -> dict[str, Any]:
    costs = step_costs(raw_dir / "_run_log.jsonl")
    cheap = detect_all(raw_dir, domains, rules_dir, workers=workers, sources=CHEAP_SOURCES)
    full = detect_all(raw_dir, domains, rules_dir, workers=workers, sources=FULL_SOURCES)
    tiered, sent = tiered_lines(cheap, full)
    cheap_cost = sum(costs.get(s, 0.0) for s in CHEAP_STEPS)
    browser_cost = costs.get(BROWSER_STEP, 0.0)
    share = len(sent) / len(domains) if domains else 0.0
    rows = [
        measure_row("cheap tier only", cheap, CHEAP_STEPS, ruleset, ground_truth, costs),
        measure_row("tiered", tiered, CHEAP_STEPS, ruleset, ground_truth, costs),
        measure_row(
            "browser for all", full, (*CHEAP_STEPS, BROWSER_STEP), ruleset, ground_truth, costs
        ),
    ]
    # The tiered row pays the browser only for the share of domains it sends.
    rows[1]["cost_s_per_domain"] = round(cheap_cost + share * browser_cost, 2)
    reasons: dict[str, int] = {}
    for reason in sent.values():
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "rows": rows,
        "sent": sent,
        "reasons": reasons,
        "domains": len(domains),
        "step_costs": costs,
    }


def render(result: dict[str, Any], meta: dict[str, Any]) -> str:
    cheap, tiered, full = result["rows"]
    sent = result["sent"]
    n = result["domains"]
    lost = sorted(set(full["core_sites"]) - set(tiered["core_sites"]))
    out = [
        "# Tiered pipeline: a browser only where it pays",
        "",
        "Generated by `techdetect evaluate tiering` from the saved raw data (`data/raw/`), "
        "the rules and `data/ground_truth.json`. Do not edit by hand.",
        "",
        f"- **Run:** tool `{meta.get('tool_version', '?')}`, rules "
        f"`{meta.get('ruleset_version', '?')}`, confidence threshold {DEFAULT_MIN_CONFIDENCE} "
        "(as `techdetect detect`).",
        "- **Cheap tier** (every domain, no browser): static fetch, certificate, robots.txt, "
        "DNS and URLs inside inline scripts. The script files downloaded over plain HTTP are "
        "left out: most of what they add is a mention inside a bundle (`docs/ABLATION.md`).",
        "- **Policy** (decided from the cheap tier's result only): send a domain to the "
        "browser if the cheap tier found a shop (category Ecommerce: the client's market), "
        "a tag manager (it injects trackers only a running page shows), or a plain fetch "
        "blocked by a bot check or a TLS/connection error (a browser may get past it; a "
        "site that is down is not sent). That last rule was read off these same 200 "
        "domains, so it is tuned to them.",
        "- **Precision** uses the existing labels of the 20 labelled domains; detections "
        "made by the inline-URL approach there may have no verdict yet "
        "(column Unlabelled), so precision does not cover them (see `docs/ABLATION.md` for "
        "how those approaches were checked).",
        "- **Recall** is measured on the 10 blind domains, and 9 rules were written for "
        "misses on those same domains, so recall here is fitted to that set; 66% (75/113) "
        "is the last independent figure.",
        "- **Cost** is the average wall time per domain from the collect run log; the tiered "
        "row pays the browser only for the domains it sends. CPU and memory per browser were "
        "not measured.",
        "",
        "| Row | Domains rendered in a browser | Unique core | Unique extended | Core per "
        "domain | Precision | Unlabelled | Recall | Cost (s/domain) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    rendered = {"cheap tier only": 0, "tiered": len(sent), "browser for all": n}
    for r in (cheap, tiered, full):
        out.append(
            f"| {r['label']} | {rendered[r['label']]} of {n} | {r['unique_core']} "
            f"| {r['unique_extended']} | {r['core_per_domain']} | {rate(*r['precision'])} "
            f"| {r['unlabelled']} | {rate(*r['recall'])} | {r['cost_s_per_domain']} |"
        )
    out += [
        "",
        "Domains sent to the browser, by reason: "
        + ", ".join(f"{k} {v}" for k, v in sorted(result["reasons"].items(), key=lambda x: -x[1]))
        + ".",
        "",
        f"The tiered row keeps {tiered['unique_core']} of the {full['unique_core']} core names "
        f"of the browser-for-all row while rendering {len(sent)} of {n} domains. "
        f"Names lost ({len(lost)}), with the number of sites where browser-for-all finds "
        "them: " + (", ".join(f"{x} ({full['core_sites'][x]})" for x in lost) or "none") + ".",
    ]
    return "\n".join(out) + "\n"
