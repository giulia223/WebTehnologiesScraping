"""From raw matches to one result per technology: confidence and versions.

Confidence is the rule author's estimate of how strong a clue is (an untagged pattern
counts 100, "\\;confidence:50" counts 50). It is not a measured probability of being
right; measured accuracy is precision, against hand-checked labels.

Combining follows Wappalyzer's `resolve()`, with one deliberate change:
  - Wappalyzer:  confidence = Math.min(100, confidence + pattern.confidence) for every
                 match, so one weak pattern seen on two pages became "certain";
  - here:        matches are grouped by pattern first, and each pattern adds its
                 confidence once, however often and wherever it matched. Different
                 patterns still add up, and the total is capped at 100.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from techdetect.fingerprints.matcher import Evidence

MAX_EXAMPLES = 3
# Version rules copied from Wappalyzer (resolveVersion / resolve in wappalyzer.js):
MAX_GROUP_CHARS = 10  # a captured group longer than this is not used as a version part
MAX_VERSION_CHARS = 15  # a longer "version" is not one
MAX_VERSION_NUMBER = 10_000  # "Ignore long numeric strings like timestamps"
_LEADING_NUMBER = re.compile(r"\s*[+-]?\d+")
_LEFTOVER_REFERENCE = re.compile(r"\\\d")


def combine_confidence(confidences: Iterable[int]) -> int:
    """Sum capped at 100, as Wappalyzer does. Kept in one place so another formula
    (e.g. noisy-OR: 1 - product of (1 - c)) can be swapped in and compared."""
    return min(100, sum(confidences))


def resolve_version(template: str, full_match: str, groups: tuple[str | None, ...]) -> str:
    """Fill a version template such as "\\1" or "\\1?4:" from a match's groups.

    Follows Wappalyzer's resolveVersion(): group N replaces "\\N"; "\\N?a:b" becomes "a"
    if group N matched something, else "b"; a group longer than 10 characters is not
    used; references left over at the end are removed. (Wappalyzer applied each ternary
    to the original template; here it is applied to the result so far, which only
    differs for a template with two ternaries, none of which exist in the rules.)
    """
    if not template:
        return ""
    resolved = template
    for index, value in enumerate((full_match, *groups)):
        if value is not None and len(value) > MAX_GROUP_CHARS:
            continue
        ternary = re.search(rf"\\{index}\?([^:]+):(.*)$", resolved)
        if ternary:
            resolved = resolved.replace(
                ternary.group(0), ternary.group(1) if value else ternary.group(2)
            )
        resolved = resolved.strip().replace(f"\\{index}", value or "")
    return _LEFTOVER_REFERENCE.sub("", resolved).strip()


def plausible_version(version: str) -> bool:
    """Wappalyzer's filter: not empty, at most 15 characters, not a timestamp-like number."""
    if not version or len(version) > MAX_VERSION_CHARS:
        return False
    number = _LEADING_NUMBER.match(version)
    return int(number.group(0)) < MAX_VERSION_NUMBER if number else True


@dataclass(frozen=True)
class PatternGroup:
    """Every match of one pattern of one technology: it counts once."""

    signal: str
    key: str | None
    subkey: str | None
    pattern: str
    confidence: int
    tier: str
    match_count: int
    artifacts: tuple[str, ...]  # every artifact it matched in (static.html, ...)
    versions: tuple[str, ...]
    examples: tuple[Evidence, ...]  # up to MAX_EXAMPLES, different locations first


@dataclass(frozen=True)
class TechnologyResult:
    name: str
    confidence: int
    versions: tuple[str, ...]  # every distinct version found, in order of discovery
    tier: str  # "observed" if any group is, else "declared"; "inferred" if only implied
    groups: tuple[PatternGroup, ...]  # empty for an inferred technology
    implied_by: str | None = None  # the technology whose `implies` rule added it


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _examples(evidence: list[Evidence]) -> tuple[Evidence, ...]:
    """Up to MAX_EXAMPLES matches, preferring different places (artifact + location)."""
    chosen: list[Evidence] = []
    seen: set[tuple[str, str | None]] = set()
    for e in evidence:
        if (e.source_artifact, e.location) not in seen:
            seen.add((e.source_artifact, e.location))
            chosen.append(e)
    for e in evidence:  # fill up with repeats if there are fewer places than examples
        if len(chosen) >= MAX_EXAMPLES:
            break
        if e not in chosen:
            chosen.append(e)
    return tuple(chosen[:MAX_EXAMPLES])


def _group(evidence: list[Evidence]) -> PatternGroup:
    first = evidence[0]
    versions = (resolve_version(e.version_template, e.matched_value, e.groups) for e in evidence)
    return PatternGroup(
        signal=first.signal,
        key=first.key,
        subkey=first.subkey,
        pattern=first.pattern,
        confidence=first.confidence,
        tier=first.tier,
        match_count=len(evidence),
        artifacts=_unique(e.source_artifact for e in evidence),
        versions=_unique(v for v in versions if plausible_version(v)),
        examples=_examples(evidence),
    )


def combine(evidence: Iterable[Evidence]) -> dict[str, TechnologyResult]:
    """One result per technology, from all of a domain's raw matches."""
    by_pattern: dict[str, dict[tuple, list[Evidence]]] = {}
    for e in evidence:
        # One pattern = the same rule text in the same field, under the same name.
        key = (e.signal, e.key, e.subkey, e.pattern)
        by_pattern.setdefault(e.technology, {}).setdefault(key, []).append(e)

    results: dict[str, TechnologyResult] = {}
    for name in sorted(by_pattern):
        groups = tuple(_group(matches) for matches in by_pattern[name].values())
        results[name] = TechnologyResult(
            name=name,
            confidence=combine_confidence(g.confidence for g in groups),
            versions=_unique(v for g in groups for v in g.versions),
            tier="observed" if any(g.tier == "observed" for g in groups) else "declared",
            groups=groups,
        )
    return results
