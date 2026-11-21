"""The browser as referee for the cheap, no-browser approaches (Tasks 7.3 and 7.4).

Static+: is a URL inside an inline script good enough proof without a browser? (7.3)
For the script files downloaded over plain HTTP (7.4) a request is not enough: the file
whose text matched is almost always loaded, but text inside a bundle is often a mention
(Google's gtag runtime names Floodlight on every site, a consent manager lists hundreds
of vendors). There an addition is confirmed only if the browser run found the same
technology on the same site by its own evidence (`browser_row_verdict`).

The static+ row matches script-URL rules against URLs written in the static page's inline
scripts (`--inline-urls`). Each technology it adds is checked against the browser data we
already have for the same domain: if the browser requested a URL that the same rule
matches, the addition is **confirmed** (the script really loaded). If the browser did not,
it is **not confirmed**: a mention, a tag waiting for cookie consent, or a script the
browser was blocked from. Those are judged by a separate reviewer (docs/DEFINITIONS.md);
their verdicts live in data/static_plus_review.json.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from techdetect.detect import counted
from techdetect.fingerprints.js_files import GTM_URL_KEY
from techdetect.fingerprints.loader import RuleSet
from techdetect.fingerprints.matcher import INLINE_URL_KEY, REGEX_TIMEOUT_S

REVIEW_FILE = Path("data/static_plus_review.json")


def core_names(line: dict[str, Any]) -> set[str]:
    return {t["name"] for t in line["technologies"] if t["group"] == "core"}


def from_inline_urls(evidence: dict[str, Any]) -> bool:
    """Evidence that static+ added: a URL inside an inline script."""
    return evidence.get("key") == INLINE_URL_KEY


def from_js_files(evidence: dict[str, Any]) -> bool:
    """Evidence that the downloaded script files added: their text, or a URL inside a GTM
    container."""
    return evidence["signal"] == "scripts" or evidence.get("key") == GTM_URL_KEY


def additions(
    static_lines: list[dict[str, Any]],
    plus_lines: list[dict[str, Any]],
    is_new: Callable[[dict[str, Any]], bool] = from_inline_urls,
) -> list[dict[str, Any]]:
    """Every (domain, technology) that is core in the second row but not in the first, on
    a counted domain, with the new evidence (`is_new`) that produced it."""
    before = {line["domain"]: core_names(line) for line in static_lines}
    found = []
    for line in plus_lines:
        if not counted(line):
            continue
        for tech in line["technologies"]:
            if tech["group"] != "core" or tech["name"] in before.get(line["domain"], set()):
                continue
            inline = [e for e in tech["evidence"] if is_new(e)]
            found.append(
                {
                    "domain": line["domain"],
                    "name": tech["name"],
                    "evidence": [
                        {
                            "signal": e["signal"],
                            "pattern": e["pattern"],
                            "matched_value": e["examples"][0]["matched_value"],
                            "context": e["examples"][0].get("context", ""),
                            "location": e["examples"][0].get("location"),
                        }
                        for e in inline
                    ],
                }
            )
    return found


def browser_requests(raw_dir: Path, domain: str) -> list[str] | None:
    """URLs the browser requested on this domain, or None if it got no real page."""
    path = raw_dir / domain / "browser.json"
    if not path.is_file():
        return None
    browser = json.loads(path.read_text(encoding="utf-8"))
    if browser.get("status") != "ok":
        return None
    # Only network requests: data: and blob: URLs are page-made content (a data: URL can
    # be megabytes long), never a script fetched from a server.
    return [
        r["url"] for r in browser.get("requests", []) if r["url"].startswith(("http:", "https:"))
    ]


def _host(url: str) -> str:
    rest = url.split("//", 1)[-1]
    return rest.split("/", 1)[0].split(":", 1)[0].lower()


def browser_verdict(addition: dict[str, Any], requests: list[str] | None, ruleset: RuleSet) -> str:
    """confirmed / not_confirmed / no_browser_page, from the browser's request list.

    The same rule that matched the URL must match a URL the browser requested (scriptSrc
    rules on the URL, xhr rules on its host, as in normal matching). For a match in a
    downloaded file's text ("scripts" rules), the browser must have loaded that file."""
    if requests is None:
        return "no_browser_page"
    tech = ruleset.technologies[addition["name"]]
    loaded = {url.split("#", 1)[0] for url in requests}
    for e in addition["evidence"]:
        if e["signal"] == "scripts":
            if (e["location"] or "").split("#", 1)[0] in loaded:
                return "confirmed"
            continue
        for pattern in tech.patterns:
            if pattern.field != e["signal"] or pattern.source != e["pattern"]:
                continue
            for url in requests:
                value = _host(url) if pattern.field == "xhr" else url
                try:
                    if pattern.regex.search(value, timeout=REGEX_TIMEOUT_S):
                        return "confirmed"
                except TimeoutError:  # as in the matcher: a slow pattern never hangs a run
                    continue
    return "not_confirmed"


def browser_row_verdict(addition: dict[str, Any], browser_line: dict[str, Any] | None) -> str:
    """confirmed / not_confirmed / no_browser_page: did the browser run (without the cheap
    approach being tested) find the same technology on the same site?"""
    if browser_line is None or browser_line.get("artifacts", {}).get("rendered.html") != "used":
        return "no_browser_page"
    return "confirmed" if addition["name"] in core_names(browser_line) else "not_confirmed"


def referee(
    raw_dir: Path,
    static_lines: list[dict[str, Any]],
    plus_lines: list[dict[str, Any]],
    browser_core: set[str],
    static_core: set[str],
    ruleset: RuleSet,
    is_new: Callable[[dict[str, Any]], bool] = from_inline_urls,
    browser_lines: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The check of one cheap approach, as plain data for ablation.json and the report.

    With `browser_lines` (the + browser row), an addition is confirmed only if that row
    has the same technology on the same site; without, by the browser's requests."""
    items = additions(static_lines, plus_lines, is_new)
    by_domain = {line["domain"]: line for line in browser_lines or []}
    for item in items:
        if browser_lines is not None:
            item["browser"] = browser_row_verdict(item, by_domain.get(item["domain"]))
        else:
            item["browser"] = browser_verdict(
                item, browser_requests(raw_dir, item["domain"]), ruleset
            )
    plus_core = {n for line in plus_lines if counted(line) for n in core_names(line)}
    browser_only = browser_core - static_core
    return {
        "additions": items,
        "browser_only_names": len(browser_only),
        "recovered_names": sorted(browser_only & plus_core),
        # Claimed by static+ but never core once the browser is added: the browser did not
        # see these products work anywhere in the list.
        "not_in_browser_row": sorted(plus_core - browser_core - static_core),
    }


def load_review(path: Path = REVIEW_FILE) -> dict[tuple[str, str], dict[str, str]]:
    """Reviewer verdicts on not-confirmed additions: (domain, name) -> {verdict, reason}."""
    if not path.is_file():
        return {}
    return {(r["domain"], r["name"]): r for r in json.loads(path.read_text(encoding="utf-8"))}
