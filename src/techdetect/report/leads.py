"""Lead list for the Shopify analytics vendor (Task 8.1): one row of facts per domain.

The salesperson needs to know, per site: is it a Shopify store (and the proof), what
technologies it uses, and which of those only a browser could see. No score and no
"competitor" label: the human chose facts only, and the client is context for the story,
not something the tool models (decision F8.1).

Rows come in input order. Domains whose page is not the owner's website are listed with
a note and no technologies, because what was found there belongs to someone else: a
redirect to another company's site (at the bottom, "redirects to <target>"), a parking
page, a placeholder page. Everything is read from detections.jsonl; nothing is re-matched.
"""

import csv
import io
from pathlib import Path
from typing import Any

from techdetect.collect.storage import write_atomic
from techdetect.detect import NOT_THE_SITE, counted

COLUMNS = (
    "domain",
    "status",
    "note",
    "shopify",
    "shopify_evidence",
    "technology_count",
    "technologies",
    "only_seen_by_browser",
    "dns_only",
)
# The browser's files. A technology whose evidence comes only from these was invisible in
# the plain HTML, headers, certificate, robots.txt and DNS (hypothesis H7).
BROWSER_ARTIFACTS = frozenset({"rendered.html", "browser.json"})
SEPARATOR = "; "


def only_seen_by_browser(tech: dict[str, Any]) -> bool:
    artifacts = {a for e in tech["evidence"] for a in e.get("artifacts", [])}
    return bool(artifacts) and artifacts <= BROWSER_ARTIFACTS


def evidence_line(tech: dict[str, Any]) -> str:
    """The first piece of evidence in one line, verifiable in a browser's DevTools."""
    e = tech["evidence"][0]
    x = e["examples"][0]
    key = f" {e['key']}" if e.get("key") else ""
    return (
        f"{e['signal']}{key}: '{x['matched_value']}' in {x['source_artifact']} "
        f"at {x.get('location') or '-'}"
    )


def redirects_elsewhere(line: dict[str, Any]) -> bool:
    """The domain's website is another company's (summary.json's "redirected_elsewhere")."""
    return (
        bool(line.get("redirected_to"))
        and not line.get("redirect_same_name")
        and line["status"] not in NOT_THE_SITE
    )


def note(line: dict[str, Any]) -> str:
    """Why a domain has no (or few) technologies, in words a salesperson understands."""
    target = line.get("redirected_to")
    status = line["status"]
    # A parked domain often redirects to a parking lander: being parked is the fact that
    # matters, not where the lander lives (as in summary.json).
    if status == "parked":
        return "parked or for-sale page, no website"
    if status == "placeholder":
        return "placeholder page (hosting default or coming soon), no website yet"
    if redirects_elsewhere(line):
        return f"redirects to {target}, another company's site: its technologies are not listed"
    if status == "bot_challenge":
        return "a bot check blocked the page: only DNS and certificate facts"
    if status.startswith("failed:"):
        return f"site not reachable ({status.removeprefix('failed:')})"
    if target:
        return f"redirects to {target}, the same company: counted for this domain"
    return ""


def lead_row(line: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {c: "" for c in COLUMNS}
    row.update(domain=line["domain"], status=line["status"], note=note(line))
    if not counted(line):  # another company's site, a parking or placeholder page
        row.update(shopify="no", technology_count=0)
        return row
    core = sorted(
        (t for t in line["technologies"] if t["group"] == "core"), key=lambda t: t["name"]
    )
    shopify = next((t for t in core if t["name"] == "Shopify"), None)
    row.update(
        shopify="yes" if shopify else "no",
        shopify_evidence=evidence_line(shopify) if shopify else "",
        technology_count=len(core),
        technologies=SEPARATOR.join(t["name"] for t in core),
        only_seen_by_browser=SEPARATOR.join(t["name"] for t in core if only_seen_by_browser(t)),
        dns_only=SEPARATOR.join(
            sorted(t["name"] for t in line["technologies"] if t["group"] == "dns_only")
        ),
    )
    return row


def build_leads(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per input domain, in input order; redirects to another company last."""
    elsewhere = [line for line in lines if redirects_elsewhere(line)]
    rest = [line for line in lines if not redirects_elsewhere(line)]
    return [lead_row(line) for line in rest + elsewhere]


def write_leads(rows: list[dict[str, Any]], output_dir: Path) -> Path:
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path = output_dir / "leads.csv"
    write_atomic(path, text.getvalue().encode("utf-8"))
    return path
