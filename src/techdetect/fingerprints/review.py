"""`techdetect candidates review`: a human decides on each proposed rule, one by one.

For each candidate it shows the rule, its source and how sure we are of the source, and
what the rule actually matches on its example domains' saved data (offline). The human
answers accept / reject / skip; only that answer ever sets a rule to `accepted`.
"""

import json
from pathlib import Path
from typing import Any

import typer

from techdetect.fingerprints.candidates import Candidate, load_candidates, problems, set_status
from techdetect.fingerprints.loader import (
    LIST_FIELDS,
    LoadReport,
    RuleSet,
    parse_technology,
)
from techdetect.fingerprints.matcher import RuleIndex, match_domain

MAX_MATCHES_SHOWN = 3
RULE_FIELDS = (*LIST_FIELDS, "headers", "cookies", "meta", "js", "dns", "dom", "implies")


def preview_ruleset(candidate: Candidate, base: RuleSet) -> RuleSet:
    """A rule set holding only this candidate, to see what it would match."""
    report = LoadReport()
    tech = parse_technology(candidate.name, candidate.rule, "candidates", report)
    return RuleSet({candidate.name: tech}, base.categories, base.groups, "preview", report)


def show_candidate(
    index: int, total: int, candidate: Candidate, base: RuleSet, raw_dir: Path
) -> None:
    rule = candidate.rule
    categories = [base.categories[c].name for c in rule.get("cats", []) if c in base.categories]
    typer.echo(f"\n[{index}/{total}] {candidate.name}   ({candidate.file.name})")
    typer.echo(f"  categories: {', '.join(categories) or '-'}   origin: {rule.get('origin')}")
    for name in RULE_FIELDS:
        if name in rule:
            typer.echo(f"  {name}: {json.dumps(rule[name], ensure_ascii=False)}")
    verified = (
        "checked: page fetched, quote found"
        if rule.get("source_verified")
        else ("NOT checked: nobody confirmed the page says this")
    )
    typer.echo(f"  source: {rule.get('source_url')}  ({verified})")
    typer.echo(f"  quote: {rule.get('source_quote')!r}")
    typer.echo(f"  seen on {rule.get('seen_on', '?')} domains, e.g. {rule.get('examples', [])}")
    for problem in problems(candidate):
        typer.echo(f"  PROBLEM: {problem}")

    index_ = RuleIndex(preview_ruleset(candidate, base))
    for domain in rule.get("examples", []):
        if not (raw_dir / domain).is_dir():
            typer.echo(f"  {domain}: no saved data to check against")
            continue
        evidence = match_domain(raw_dir, domain, index_).evidence
        typer.echo(f"  {domain}: {len(evidence)} matches")
        for e in evidence[:MAX_MATCHES_SHOWN]:
            typer.echo(
                f"    {e.signal} in {e.source_artifact} at {e.location}: {e.matched_value[:100]!r}"
            )


def review_candidates(candidates_dir: Path, base: RuleSet, raw_dir: Path) -> dict[str, int]:
    """Ask about every `proposed` candidate. Returns how many got each answer."""
    proposed = [c for c in load_candidates(candidates_dir) if c.rule.get("status") == "proposed"]
    counts = {"accepted": 0, "rejected": 0, "skipped": 0}
    if not proposed:
        typer.echo(f"no proposed rules in {candidates_dir}")
        return counts
    for index, candidate in enumerate(proposed, start=1):
        show_candidate(index, len(proposed), candidate, base, raw_dir)
        answer = typer.prompt("  [a]ccept / [r]eject / [s]kip / [q]uit").strip().lower()[:1]
        if answer == "q":
            break
        status = {"a": "accepted", "r": "rejected"}.get(answer)
        if status is None:
            counts["skipped"] += 1
            continue
        note = typer.prompt("  note (why)", default="", show_default=False)
        set_status(candidate, status, note)  # saved at once
        counts[status] += 1
    typer.echo(
        f"accepted {counts['accepted']}, rejected {counts['rejected']}, "
        f"skipped {counts['skipped']}. Accepted rules count only after `candidates promote`."
    )
    return counts


def candidate_summary(candidates_dir: Path) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for candidate in load_candidates(candidates_dir):
        status = str(candidate.rule.get("status"))
        by_status[status] = by_status.get(status, 0) + 1
    return by_status
