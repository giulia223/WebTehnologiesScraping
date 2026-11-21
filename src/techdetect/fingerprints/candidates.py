"""Candidate rules: rules proposed by a script or an AI agent, waiting for human review.

A candidate lives in fingerprints/candidates/*.json, in webappanalyzer's format plus
review fields. It reaches the results only after a human marks it `accepted` and runs
`promote`, which moves it into fingerprints/custom/ stamped `reviewed_by: human`. The
loader refuses custom rules without that stamp, so nothing unreviewed is ever counted.

Review fields:
  status           proposed / accepted / rejected (only a human sets accepted)
  seen_on          on how many of our domains it matched
  examples         up to 2 of those domains
  source_url       a page that proves the claim (usually the vendor's own)
  source_quote     the sentence on that page that proves it
  source_verified  true only if the page was actually fetched and the quote found on it:
                   an AI can invent a plausible URL and quote; this flag says it was checked
  origin           data (seen in our crawl) / entity_list (from an open list) /
                   llm_suggested (an AI's idea, the least trusted)
  review_note      the reviewer's note
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from techdetect.collect.storage import write_json_atomic

STATUSES = ("proposed", "accepted", "rejected")
ORIGINS = ("data", "entity_list", "llm_suggested")
REQUIRED_REVIEW_FIELDS = ("status", "origin", "source_url", "source_quote", "source_verified")
REVIEWED_BY = "human"


@dataclass(frozen=True)
class Candidate:
    file: Path
    name: str
    rule: dict[str, Any]


def read_rules_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected an object of technology name -> rule")
    return data


def load_candidates(candidates_dir: Path) -> list[Candidate]:
    return [
        Candidate(path, name, rule)
        for path in sorted(candidates_dir.glob("*.json"))
        for name, rule in read_rules_file(path).items()
    ]


def problems(candidate: Candidate) -> list[str]:
    """What is wrong with a candidate's review fields (empty list = well-formed)."""
    rule = candidate.rule
    found = [f"missing {name}" for name in REQUIRED_REVIEW_FIELDS if name not in rule]
    if rule.get("status") not in STATUSES:
        found.append(f"status must be one of {list(STATUSES)}")
    if "origin" in rule and rule["origin"] not in ORIGINS:
        found.append(f"origin must be one of {list(ORIGINS)}")
    if not isinstance(rule.get("source_verified"), bool):
        found.append("source_verified must be true or false")
    if len(rule.get("examples", [])) > 2:
        found.append("at most 2 examples")
    return found


def set_status(candidate: Candidate, status: str, note: str) -> None:
    """Record a human decision in the candidate's file (the only way to accept a rule)."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    data = read_rules_file(candidate.file)
    data[candidate.name]["status"] = status
    if note:
        data[candidate.name]["review_note"] = note
    write_json_atomic(candidate.file, data)


def promote(candidates_dir: Path, custom_dir: Path) -> list[str]:
    """Move every `accepted` candidate into custom/ (same file name), stamped as reviewed.

    Only `accepted` moves: `proposed` (not reviewed yet) and `rejected` stay where they
    are. A name already in the custom file is never overwritten: the run stops and says so.
    """
    moved: list[str] = []
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    for path in sorted(candidates_dir.glob("*.json")):
        candidates = read_rules_file(path)
        accepted = {n: r for n, r in candidates.items() if r.get("status") == "accepted"}
        if not accepted:
            continue
        target = custom_dir / path.name
        custom = read_rules_file(target) if target.is_file() else {}
        clashes = sorted(set(accepted) & set(custom))
        if clashes:
            raise ValueError(f"{target} already has {clashes}: not overwritten")
        for name, rule in accepted.items():
            custom[name] = {**rule, "reviewed_by": REVIEWED_BY, "reviewed_at": today}
            del candidates[name]
            moved.append(f"{name} ({path.name})")
        # The custom file first: if the run stops in between, the rule exists twice
        # (visible, harmless) rather than nowhere.
        write_json_atomic(target, custom)
        write_json_atomic(path, candidates)
    return moved
