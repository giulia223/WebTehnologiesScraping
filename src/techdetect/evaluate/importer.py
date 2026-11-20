"""Import the blind labels written in docs/manual_inspect_domains.md into ground_truth.json.

The inspection file holds, per domain, a technology table, a list of plugins / themes /
apps, and DNS lines naming providers ("-> Google Workspace"). Each entry becomes a
`human_found` record: the name as written, the rule name it corresponds to (if any), its
version, where it was seen, and who found it. Nothing is guessed: a name that matches no
rule exactly (or through the small alias list below) is kept as written, with
`in_rules: false` and suggestions, and listed in the import report for a human to check.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from techdetect.evaluate.labels import domain_record, match_name, now

# The same product under another name in the rules. Only unambiguous renames belong here
# (each one checked against the rule's website); anything doubtful stays unmatched.
ALIASES: dict[str, str] = {
    "Google Fonts": "Google Font API",  # rule website fonts.google.com
    "Matomo": "Matomo Analytics",  # matomo.org; the labels saw its matomo.js tracker
    "WPBakery Page Builder": "wpBakery",  # wpbakery.com
    "Twemoji": "Twitter Emoji (Twemoji)",  # twitter.github.io/twemoji
    "Vogue": "Kaira Vogue",  # the WordPress theme "vogue" (kairaweb.com/wordpress-theme/vogue)
    "IONOS MyWebsite": "MyWebsite",  # ionos.com; the page's generator says "IONOS MyWebsite"
}
# DNS lines name a provider's service ("World4You mail", "OVHcloud DNS"); the rules name
# the company, so the service word is dropped for DNS entries.
_DNS_SERVICE_WORD = re.compile(r"\s+(?:mail|email|dns)$", re.I)

_SECTION = re.compile(r"^## (\S+)\s*$")
_VERSION = re.compile(r"^(.*?)\s+v?(\d[\w.\-]*)$")
_DNS_LINE = re.compile(r"^\s*-\s*(MX|TXT / SPF|NS):(.*)$")


@dataclass
class ParsedDomain:
    domain: str
    checked: str = ""
    status: str = ""
    entries: list[dict[str, Any]] = field(default_factory=list)


def split_name(text: str) -> str:
    """The name as written, without a bracketed note: "Matomo (self-hosted)" -> "Matomo".

    A trailing version is NOT removed here: "Microsoft 365" and "Amazon Route 53" end in a
    number that is part of the name, so resolve_names tries the full name first.
    """
    text = text.strip().strip("`").strip()
    return re.split(r"\s*\(", text, maxsplit=1)[0].strip()


def without_version(name: str) -> tuple[str, str]:
    """ "jQuery 3.7.1" -> ("jQuery", "3.7.1"); "Matomo" -> ("Matomo", "")."""
    match = _VERSION.match(name)
    return (match.group(1).strip(), match.group(2)) if match else (name, "")


def _plugin_entries(item: str) -> list[tuple[str, str, str]]:
    """One "- Name 1.2 (plugin): where" line -> [(name, version, where)]."""
    if item.lower().startswith("none"):
        # "none (Wix). Wix apps seen: Wix FAQ (...); Wix Forms (...)": the apps still count.
        if "apps seen:" not in item:
            return []
        apps = item.split("apps seen:", 1)[1]
        return [(split_name(app), "", item) for app in apps.split(";") if app.strip()]
    head, _, where = item.partition(":")
    return [(split_name(head), "", where.strip())]


def parse_inspection_file(text: str) -> dict[str, ParsedDomain]:
    domains: dict[str, ParsedDomain] = {}
    current: ParsedDomain | None = None
    part = ""  # "table", "plugins", "dns", or "" (anything else)
    for line in text.splitlines():
        if match := _SECTION.match(line):
            current = domains.setdefault(match.group(1), ParsedDomain(match.group(1)))
            part = ""
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("```") or stripped.lower().startswith("raw notes"):
            current = None  # a domain's raw notes end its structured part
            continue
        if stripped.startswith("- Checked:"):
            current.checked = stripped.removeprefix("- Checked:").strip()
        elif stripped.startswith("- Status:"):
            current.status = stripped.removeprefix("- Status:").strip()
        elif stripped.startswith("| Technology"):
            part = "table"
        elif stripped.startswith("WordPress plugins / themes / Shopify apps"):
            part = "plugins"
        elif stripped.startswith("DNS only"):
            part = "dns"
        elif stripped.endswith(":") and not stripped.startswith("-"):
            part = ""  # "After consent only:", "Notes (anything odd):", ...
        elif part == "table" and stripped.startswith("|") and not stripped.startswith("|---"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) >= 2 and cells[0]:
                current.entries.append(
                    {
                        "typed": split_name(cells[0]),
                        "version": "",
                        "where": cells[1],
                        "section": "website",
                    }
                )
        elif part == "plugins" and stripped.startswith("- "):
            for name, version, where in _plugin_entries(stripped[2:]):
                current.entries.append(
                    {"typed": name, "version": version, "where": where, "section": "plugin"}
                )
        elif part == "dns" and (match := _DNS_LINE.match(line)) and "->" in match.group(2):
            record, _, provider = match.group(2).partition("->")
            provider = provider.split(";")[0]  # "X mail; `google-site-verification=...`"
            provider = provider.split(" / ")[0]  # "HostGator / websitewelcome mail": one host
            name = _DNS_SERVICE_WORD.sub("", split_name(provider))
            current.entries.append(
                {
                    "typed": name,
                    "version": "",
                    "where": f"DNS {match.group(1)}: {record.strip()}",
                    "section": "dns",
                }
            )
    return domains


def found_by(checked: str) -> str:
    """Who made the label, from its "Checked:" line: a person in Chrome DevTools, or the
    separate AI session that inspected with a headless browser."""
    return "blind_session" if "headless" in checked.lower() else "me"


def resolve_names(parsed: ParsedDomain, names: list[str]) -> list[dict[str, Any]]:
    """Match each entry to a rule name; one record per distinct technology."""
    records: dict[str, dict[str, Any]] = {}
    for entry in parsed.entries:
        typed = entry["typed"]
        base, version = without_version(typed)
        # The name as written first ("Microsoft 365"), then without a version ("jQuery").
        exact, suggestions = None, []
        for candidate, candidate_version in ((typed, ""), (base, version)):
            exact, suggestions = match_name(ALIASES.get(candidate, candidate), names)
            if exact:
                entry = {**entry, "version": entry["version"] or candidate_version}
                break
        if not exact:
            typed, entry = base, {**entry, "version": entry["version"] or version}
        technology = exact or typed
        if technology in records:  # e.g. a plugin listed in the table and the plugin list
            continue
        records[technology] = {
            "technology": technology,
            "typed": typed,
            "in_rules": exact is not None,
            "suggestions": [] if exact else suggestions,
            "version": entry["version"],
            "where": entry["where"],
            "section": entry["section"],
            "found_by": found_by(parsed.checked),
            "at": now(),
            "imported_from": "docs/manual_inspect_domains.md",
        }
    return list(records.values())


def import_blind(
    data: dict[str, Any], parsed: dict[str, ParsedDomain], names: list[str], force: bool
) -> dict[str, str]:
    """Write each blind domain's list into `data`. Returns what happened per domain."""
    outcome: dict[str, str] = {}
    for domain in data["sets"]["blind"]:
        if domain not in parsed:
            outcome[domain] = "not in the inspection file"
            continue
        record = domain_record(data, domain)
        if record["human_found"] and not force:
            outcome[domain] = "kept: already has a list (use --force to replace)"
            continue
        record["human_found"] = resolve_names(parsed[domain], names)
        record["human_found_saved_at"] = now()
        record["site_status_label"] = parsed[domain].status
        outcome[domain] = f"imported {len(record['human_found'])} technologies"
    return outcome
