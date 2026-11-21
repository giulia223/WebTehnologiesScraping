"""Name check: one judged example for every core technology name (Task 4.5).

The headline is a count of technology *names* (341 in Run 3e), but precision was measured
on the detections of 20 labelled domains, which contain only part of those names. A name
that never appears on a labelled domain has never been looked at by anyone. This module
gives each such name one example detection to judge, so every name in the headline has
been checked at least once.

    sheet       pick one example per unchecked core name -> data/name_check.json
    merge       copy reviewers' verdicts (one JSON file per batch) into the sheet
    spot-check  a second reviewer, blind to the first one's verdicts, re-judges every
                "wrong" verdict plus a seeded sample of the others; where the two disagree,
                the name counts as unsure
    report      docs/NAME_CHECK.md (generated)

What one example proves: the name is really used by at least one site in the list. It
does not prove that every site with that name is right (a name on 5 sites has 1 checked).
"""

import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from techdetect.detect import NOT_THE_SITE
from techdetect.evaluate.labels import now
from techdetect.evaluate.metrics import rate
from techdetect.evaluate.metrics_report import _code

NAME_CHECK_SEED = 20261120
BATCH_SIZE = 45  # about what one reviewer can judge carefully in one session
SPOT_CHECK_SIZE = 10
VERDICTS = ("correct", "wrong", "unsure")
SHEET_FORMAT = 1
# Kept when the sheet is rebuilt, for an entry whose name and example site are unchanged.
SECOND_KEYS = (
    "verdict",
    "reason",
    "reviewer",
    "second_verdict",
    "second_reason",
    "second_reviewer",
)


def counted(line: dict[str, Any]) -> bool:
    """Same rule as the headline counts in summary.json: the domain's own site (no redirect
    to another company) and a real website (not a parking or placeholder page)."""
    credited = not line["redirected_to"] or bool(line.get("redirect_same_name"))
    return credited and line["status"] not in NOT_THE_SITE


def core_examples(lines: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """name -> {domain: detection} for every detection in the core group on a counted
    domain. A name with at least one entry is a core name, as in summary.json."""
    examples: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for line in lines:
        if not counted(line):
            continue
        for tech in line["technologies"]:
            if tech["group"] == "core":
                examples[tech["name"]][line["domain"]] = tech
    return dict(examples)


def labelled_verdicts(
    ground_truth: dict[str, Any], lines: list[dict[str, Any]]
) -> dict[str, list[tuple[str, str]]]:
    """name -> [(domain, verdict)] from the 20 labelled domains, in any group.

    Only verdicts whose detection still exists count: a verdict on a detection that a later
    run no longer makes (stale) says nothing about the current output."""
    detected = {line["domain"]: {t["name"] for t in line["technologies"]} for line in lines}
    found: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for domain, record in ground_truth["domains"].items():
        for name, verdict in record.get("verdicts", {}).items():
            if name in detected.get(domain, set()):
                found[name].append((domain, verdict["verdict"]))
    return dict(found)


def pick_domain(name: str, domains: list[str], seed: int = NAME_CHECK_SEED) -> str:
    """One site for this name, at random but reproducible.

    The generator is seeded with the seed *and the name*, so a name's pick does not change
    when other names appear or disappear (e.g. after a rule correction). A string seed is
    hashed with SHA-512 by `random`, so it does not depend on PYTHONHASHSEED."""
    return random.Random(f"{seed}:{name}").choice(sorted(domains))


def build_sheet(
    lines: list[dict[str, Any]],
    ground_truth: dict[str, Any],
    previous: dict[str, Any] | None = None,
    seed: int = NAME_CHECK_SEED,
) -> dict[str, Any]:
    """The list of names to check, one example each.

    Verdicts already given in `previous` are kept when the same name still has the same
    example domain, so regenerating after a rule correction does not throw judged work away.
    """
    examples = core_examples(lines)
    covered = labelled_verdicts(ground_truth, lines)
    old = {(e["name"], e["domain"]): e for e in (previous or {}).get("names", [])}
    entries = []
    for name in sorted(n for n in examples if n not in covered):
        sites = examples[name]
        domain = pick_domain(name, list(sites), seed)
        tech = sites[domain]
        entry = {
            "name": name,
            "domain": domain,
            "sites": len(sites),
            "categories": tech["categories"],
            "confidence": tech["confidence"],
            "tier": tech["tier"],
            "versions": tech.get("versions", []),
            "evidence": tech["evidence"],
            "batch": 0,
            "verdict": None,
            "reason": None,
            "reviewer": None,
            "second_verdict": None,
            "second_reason": None,
            "second_reviewer": None,
        }
        kept = old.get((name, domain))
        if kept:
            for key in SECOND_KEYS:
                entry[key] = kept.get(key)
        entries.append(entry)
    for i, entry in enumerate(entries):
        entry["batch"] = i // BATCH_SIZE + 1
    return {
        "format": SHEET_FORMAT,
        "created_at": now(),
        "seed": seed,
        "rule": (
            "every core name (as counted in summary.json) with no detection on a labelled "
            "domain; one example site per name, drawn with random.Random(f'{seed}:{name}')"
        ),
        "names": entries,
    }


def merge_verdicts(sheet: dict[str, Any], verdicts: list[dict[str, Any]], reviewer: str) -> int:
    """Copy reviewer verdicts into the sheet; returns how many were applied.

    A verdict is applied only if its (name, domain) is the sheet's current example, so a
    verdict about an old pick can never land on a different site."""
    by_key = {(e["name"], e["domain"]): e for e in sheet["names"]}
    applied = 0
    for item in verdicts:
        if item.get("verdict") not in VERDICTS:
            raise ValueError(f"bad verdict for {item.get('name')}: {item.get('verdict')!r}")
        if not item.get("reason"):
            raise ValueError(f"verdict for {item.get('name')} has no reason")
        entry = by_key.get((item["name"], item["domain"]))
        if entry is None:
            raise ValueError(f"not in the sheet: {item['name']} on {item['domain']}")
        entry["verdict"] = item["verdict"]
        entry["reason"] = item["reason"]
        entry["reviewer"] = reviewer
        applied += 1
    return applied


def merge_second(sheet: dict[str, Any], verdicts: list[dict[str, Any]], reviewer: str) -> int:
    """Copy the second reviewer's verdicts into the sheet; returns how many were applied."""
    by_key = {(e["name"], e["domain"]): e for e in sheet["names"]}
    for item in verdicts:
        if item.get("verdict") not in VERDICTS or not item.get("reason"):
            raise ValueError(f"bad second verdict for {item.get('name')}")
        if (item["name"], item["domain"]) not in by_key:
            raise ValueError(f"not in the sheet: {item['name']} on {item['domain']}")
    for item in verdicts:
        entry = by_key[(item["name"], item["domain"])]
        entry["second_verdict"] = item["verdict"]
        entry["second_reason"] = item["reason"]
        entry["second_reviewer"] = reviewer
    return len(verdicts)


def final_verdict(entry: dict[str, Any]) -> str | None:
    """The verdict that counts. Where the second reviewer disagrees with the first, nobody
    can settle it from the evidence, so the name counts as unsure: a disagreement can lower
    the headline, never raise it."""
    second = entry.get("second_verdict")
    if second and second != entry["verdict"]:
        return "unsure"
    return entry["verdict"]


def spot_check_pick(sheet: dict[str, Any], seed: int = NAME_CHECK_SEED) -> list[dict[str, Any]]:
    """What the second reviewer re-judges: every "wrong" verdict, plus SPOT_CHECK_SIZE
    others drawn at random (seeded) among the judged ones."""
    judged = [e for e in sheet["names"] if e["verdict"]]
    wrong = [e for e in judged if e["verdict"] == "wrong"]
    others = [e for e in judged if e["verdict"] != "wrong"]
    sample = random.Random(seed).sample(others, min(SPOT_CHECK_SIZE, len(others)))
    return wrong + sorted(sample, key=lambda e: e["name"])


@dataclass
class Summary:
    core_names: int
    covered: int  # names that appear on a labelled domain
    covered_correct: int  # ... with at least one "correct" verdict there
    covered_without_correct: list[tuple[str, list[tuple[str, str]]]]
    checked: Counter  # verdict -> count, over the sheet ("none" = not judged yet)
    gone: list[str]  # sheet names no longer core (e.g. removed by a correction)
    agree: int
    spot_checked: int

    @property
    def names_with_correct(self) -> int:
        return self.covered_correct + self.checked["correct"]


def summarise(
    lines: list[dict[str, Any]], ground_truth: dict[str, Any], sheet: dict[str, Any]
) -> Summary:
    examples = core_examples(lines)
    covered = labelled_verdicts(ground_truth, lines)
    covered_core = {n: covered[n] for n in examples if n in covered}
    with_correct = [n for n, v in covered_core.items() if any(x == "correct" for _, x in v)]
    current = [e for e in sheet["names"] if e["name"] in examples]
    gone = sorted(e["name"] for e in sheet["names"] if e["name"] not in examples)
    # The name is still core, but no longer on its example site (e.g. a correction removed
    # that detection): its verdict is about something that no longer exists, so it counts
    # as not judged until `sheet --force` picks a new example.
    checked = Counter(
        (final_verdict(e) if e["domain"] in examples[e["name"]] else None) or "none"
        for e in current
    )
    second = [e for e in current if e.get("second_verdict")]
    return Summary(
        core_names=len(examples),
        covered=len(covered_core),
        covered_correct=len(with_correct),
        covered_without_correct=sorted(
            (n, v) for n, v in covered_core.items() if n not in with_correct
        ),
        checked=checked,
        gone=gone,
        agree=sum(e["second_verdict"] == e["verdict"] for e in second),
        spot_checked=len(second),
    )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def evidence_line(entry: dict[str, Any]) -> str:
    """The first piece of evidence, in one line, as in docs/METRICS.md."""
    if not entry["evidence"]:
        return "no evidence of its own"
    e = entry["evidence"][0]
    x = e["examples"][0] if e.get("examples") else {}
    key = f" {e['key']}" if e.get("key") else ""
    pattern = e.get("pattern") or "(present)"
    return (
        f"{e['signal']}{key}, rule `{_code(pattern, 60)}` matched "
        f"`{_code(x.get('matched_value', ''), 60)}` in {x.get('source_artifact', '?')}"
    )


def _rows(entries: list[dict[str, Any]], with_second: bool = False) -> list[str]:
    head = "| Technology | Example site | Sites | Conf. | Why (first reviewer) | Evidence |"
    rule = "|---|---|---|---|---|---|"
    if with_second:
        head += " Second reviewer |"
        rule += "---|"
    rows = [head, rule]
    for e in sorted(entries, key=lambda e: e["name"]):
        row = (
            f"| {e['name']} | {e['domain']} | {e['sites']} | {e['confidence']} "
            f"| {e['verdict']}: {_code(e['reason'] or '', 300)} | {evidence_line(e)} |"
        )
        if with_second:
            second = e.get("second_verdict") or "-"
            why = f": {_code(e['second_reason'], 300)}" if e.get("second_reason") else ""
            row += f" {second}{why} |"
        rows.append(row)
    return rows


def render_report(summary: Summary, sheet: dict[str, Any], meta: dict[str, Any]) -> str:
    s = summary
    current = [e for e in sheet["names"] if e["name"] not in s.gone]
    sheet_total = len(current)
    c = s.checked
    judged = c["correct"] + c["wrong"]
    out = [
        "# Name check",
        "",
        "Generated by `python scripts/name_check.py report` from `data/name_check.json`, "
        "`data/ground_truth.json` and the current `data/output/detections.jsonl`. "
        "Do not edit by hand.",
        "",
        f"- **Run:** tool `{meta.get('tool_version', '?')}`, rules "
        f"`{meta.get('ruleset_version', '?')}`.",
        "- **Why:** the headline counts technology **names**. Precision "
        "(`docs/METRICS.md`) was measured on every detection of 20 labelled domains, but "
        "those domains contain only part of the names. This check gives every other core "
        "name one judged example.",
        f"- **How the example is picked:** one site per name, at random with a fixed seed "
        f"({sheet['seed']}; the seed is combined with the name, so a name's pick does not "
        "change when other names come or go).",
        "- **Who judged:** reviewer agents separate from the tool's author, following "
        "`docs/DEFINITIONS.md`, from the evidence and the saved raw data of the example "
        "site. A second reviewer agent, blind to the first one's verdicts, re-judged every "
        "`wrong` verdict and a seeded sample of the others (spot-check below); where the two "
        "disagree, the name counts as unsure.",
        "- **What it proves:** one correct example shows the name is really used by at "
        "least one site in the list. It does not prove every site with that name is right.",
        "",
        "## Headline",
        "",
        f"**{s.names_with_correct} of {s.core_names} core names have at least one "
        f"detection judged correct.**",
        "",
        "| | Names | Correct | Wrong | Unsure | Not judged yet |",
        "|---|---|---|---|---|---|",
        f"| On a labelled domain (judged in `docs/METRICS.md`) | {s.covered} "
        f"| {s.covered_correct} | - | - | - |",
        f"| Name check (one example each) | {sheet_total} | {c['correct']} | {c['wrong']} "
        f"| {c['unsure']} | {c['none']} |",
        "",
        f"Name-check precision (unsure left out): {rate(c['correct'], judged)}.",
        "",
        "## Spot-check of the reviewers",
        "",
    ]
    if s.spot_checked:
        out.append(
            f"A second reviewer agent re-judged {s.spot_checked} verdicts (every `wrong` one "
            "plus a seeded random sample of the others) from the same evidence and raw data, "
            "without seeing the first reviewer's verdicts. It agreed on "
            f"{rate(s.agree, s.spot_checked)}. Both reviewers are agents; no human re-judged "
            "these verdicts."
        )
        disagreements = [
            e for e in current if e.get("second_verdict") and e["second_verdict"] != e["verdict"]
        ]
        if disagreements:
            out += ["", "Disagreements (counted as unsure):", ""]
            out += _rows(disagreements, with_second=True)
    else:
        out.append("Not done yet.")
    for title, verdict in (("Judged wrong", "wrong"), ("Unsure", "unsure")):
        items = [e for e in current if final_verdict(e) == verdict]
        out += ["", f"## {title} ({len(items)})", ""]
        out += _rows(items, with_second=True) if items else ["None."]
    out += [
        "",
        f"## On a labelled domain, but never judged correct ({len(s.covered_without_correct)})",
        "",
        "These names were judged on the labelled domains, but no verdict there is "
        "`correct` (see `docs/METRICS.md` for the reasons).",
        "",
    ]
    if s.covered_without_correct:
        out += ["| Technology | Verdicts on labelled domains |", "|---|---|"]
        for name, verdicts in s.covered_without_correct:
            listed = ", ".join(f"{v} ({d})" for d, v in sorted(verdicts))
            out.append(f"| {name} | {listed} |")
    else:
        out.append("None.")
    if s.gone:
        out += [
            "",
            f"## No longer core ({len(s.gone)})",
            "",
            "In the sheet, but no longer a core name in the current output (e.g. removed by "
            "a rule correction): " + ", ".join(s.gone) + ".",
        ]
    out += [
        "",
        "## Every name checked",
        "",
        "| Technology | Example site | Sites | Verdict | Why (reviewer) |",
        "|---|---|---|---|---|",
    ]
    for e in sorted(current, key=lambda e: e["name"]):
        out.append(
            f"| {e['name']} | {e['domain']} | {e['sites']} | {final_verdict(e) or '-'} "
            f"| {_code(e['reason'] or '', 200)} |"
        )
    return "\n".join(out) + "\n"
