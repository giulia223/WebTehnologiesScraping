"""Precision, recall and F1 from the hand labels in data/ground_truth.json.

precision  of the detections that were judged, the share judged correct. Both sets count:
           every detection of the 20 labelled domains has a verdict. "unsure" is left out
           of the share and reported with its own count.
recall     of the technologies the blind inspection found on a blind domain, the share the
           tool also reports (in any group). Entries marked `counts: false` (not the site's
           under docs/DEFINITIONS.md) and entries added after seeing the tool are left out.
F1         the harmonic mean of the two.

Verdicts are matched to the *current* detections by name, and the current confidence is
used for thresholds: after a re-run, a detection without a verdict is reported as
unlabelled and a verdict whose detection is gone as stale, instead of silently skewing the
numbers. Every rate is written with its counts ("92% (119/130)"); a breakdown with fewer
than 10 judged detections says "too few to measure".
"""

from dataclasses import dataclass, field
from typing import Any

THRESHOLDS = (0, 50, 80)
MIN_N = 10  # below this, a per-group / per-category rate is noise, not a measurement
CONFIDENCE_BANDS = (("100", 100, 100), ("75-99", 75, 99), ("50-74", 50, 74), ("below 50", 0, 49))
GROUP_ORDER = ("core", "dns_only", "inferred_only", "themes")
SETS = ("blind", "review")


@dataclass
class Judged:
    """One detection of a labelled domain, with its verdict."""

    domain: str
    set_name: str
    name: str
    confidence: int
    group: str
    tier: str
    categories: list[str]
    verdict: str
    note: str
    evidence: list[dict[str, Any]]
    implied_by: str | None = None


@dataclass
class Tally:
    correct: int = 0
    wrong: int = 0
    unsure: int = 0

    def add(self, verdict: str) -> None:
        setattr(self, verdict, getattr(self, verdict) + 1)

    @property
    def judged(self) -> int:
        return self.correct + self.wrong


@dataclass
class Expected:
    """One technology the blind inspection found, and whether the tool reports it."""

    domain: str
    technology: str
    in_rules: bool
    where: str
    found: bool


@dataclass
class Labelled:
    judged: list[Judged] = field(default_factory=list)
    unlabelled: list[str] = field(default_factory=list)  # detected, no verdict yet
    stale: list[str] = field(default_factory=list)  # verdict, but no longer detected


def rate(num: int, den: int) -> str:
    """ "92% (119/130)": the counts always travel with the percentage."""
    return f"{round(100 * num / den)}% ({num}/{den})" if den else "n/a (0/0)"


def rate_if_enough(num: int, den: int) -> str:
    return rate(num, den) if den >= MIN_N else f"too few to measure ({num}/{den})"


def f1(precision: float | None, recall: float | None) -> float | None:
    if not precision or not recall:
        return None
    return 2 * precision * recall / (precision + recall)


def share(num: int, den: int) -> float | None:
    return num / den if den else None


def labelled_detections(gt: dict[str, Any], lines: dict[str, dict[str, Any]]) -> Labelled:
    out = Labelled()
    for set_name in SETS:
        for domain in gt["sets"][set_name]:
            verdicts = gt["domains"].get(domain, {}).get("verdicts", {})
            techs = lines.get(domain, {}).get("technologies", [])
            for tech in techs:
                label = verdicts.get(tech["name"])
                if label is None:
                    out.unlabelled.append(f"{domain}: {tech['name']}")
                    continue
                out.judged.append(
                    Judged(
                        domain=domain,
                        set_name=set_name,
                        name=tech["name"],
                        confidence=tech["confidence"],
                        group=tech["group"],
                        tier=tech["tier"],
                        categories=tech["categories"],
                        verdict=label["verdict"],
                        note=label.get("note", ""),
                        evidence=tech["evidence"],
                        implied_by=tech.get("implied_by"),
                    )
                )
            detected = {tech["name"] for tech in techs}
            out.stale += [f"{domain}: {name}" for name in verdicts if name not in detected]
    return out


def tally(items: list[Judged], threshold: int = 0) -> Tally:
    result = Tally()
    for item in items:
        if item.confidence >= threshold:
            result.add(item.verdict)
    return result


def expected_on_blind(
    gt: dict[str, Any], lines: dict[str, dict[str, Any]], threshold: int = 0
) -> list[Expected]:
    out = []
    for domain in gt["sets"]["blind"]:
        reported = {
            tech["name"].lower()
            for tech in lines.get(domain, {}).get("technologies", [])
            if tech["confidence"] >= threshold
        }
        for entry in gt["domains"].get(domain, {}).get("human_found", []):
            if entry.get("counts") is False or entry.get("after_seeing_tool"):
                continue
            out.append(
                Expected(
                    domain=domain,
                    technology=entry["technology"],
                    in_rules=bool(entry.get("in_rules")),
                    where=entry.get("where", ""),
                    found=entry["technology"].lower() in reported,
                )
            )
    return out


def band_of(confidence: int) -> str:
    return next(name for name, low, high in CONFIDENCE_BANDS if low <= confidence <= high)


def _breakdown(items: list[Judged], key: Any) -> dict[str, Tally]:
    tallies: dict[str, Tally] = {}
    for item in items:
        for value in key(item):
            tallies.setdefault(value, Tally()).add(item.verdict)
    return tallies


def compute(gt: dict[str, Any], lines: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Every number of the metrics report, as plain data (written to metrics.json)."""
    labelled = labelled_detections(gt, lines)
    judged = labelled.judged
    headline = []
    for threshold in THRESHOLDS:
        both = tally(judged, threshold)
        per_set = {s: tally([j for j in judged if j.set_name == s], threshold) for s in SETS}
        expected = expected_on_blind(gt, lines, threshold)
        found = sum(e.found for e in expected)
        with_rule = [e for e in expected if e.in_rules]
        precision = share(both.correct, both.judged)
        recall = share(found, len(expected))
        headline.append(
            {
                "threshold": threshold,
                "precision": [both.correct, both.judged],
                "unsure": both.unsure,
                "precision_blind": _pair(per_set["blind"]),
                "precision_review": _pair(per_set["review"]),
                "recall": [found, len(expected)],
                "recall_with_rule": [sum(e.found for e in with_rule), len(with_rule)],
                "f1": f1(precision, recall),
            }
        )
    return {
        "labelled_domains": {s: list(gt["sets"][s]) for s in SETS},
        "judged": len(judged),
        "unlabelled": labelled.unlabelled,
        "stale": labelled.stale,
        "headline": headline,
        "by_group": _as_pairs(_breakdown(judged, lambda j: [j.group])),
        "by_tier": _as_pairs(_breakdown(judged, lambda j: [j.tier])),
        "by_confidence": _as_pairs(_breakdown(judged, lambda j: [band_of(j.confidence)])),
        "by_category": _as_pairs(_breakdown(judged, lambda j: j.categories)),
        "false_positives": [_detection(j) for j in judged if j.verdict == "wrong"],
        "unsure": [_detection(j) for j in judged if j.verdict == "unsure"],
        "missed": [vars(e) for e in expected_on_blind(gt, lines) if not e.found],
        "not_counted": [
            {"domain": d, "technology": e["technology"], "why": e.get("not_counted_because", "")}
            for d in gt["sets"]["blind"]
            for e in gt["domains"].get(d, {}).get("human_found", [])
            if e.get("counts") is False
        ],
    }


def _pair(t: Tally) -> list[int]:
    return [t.correct, t.judged]


def _as_pairs(tallies: dict[str, Tally]) -> dict[str, dict[str, int]]:
    return {
        key: {"correct": t.correct, "wrong": t.wrong, "unsure": t.unsure}
        for key, t in sorted(tallies.items())
    }


def _detection(j: Judged) -> dict[str, Any]:
    first = j.evidence[0] if j.evidence else {}
    example = (first.get("examples") or [{}])[0]
    return {
        "domain": j.domain,
        "set": j.set_name,
        "technology": j.name,
        "confidence": j.confidence,
        "group": j.group,
        "tier": j.tier,
        "reason": j.note,
        "implied_by": j.implied_by,
        "signal": " ".join(filter(None, (first.get(k) for k in ("signal", "key", "subkey")))),
        "pattern": first.get("pattern", ""),
        "matched": example.get("matched_value", ""),
        "artifact": example.get("source_artifact", ""),
        "location": example.get("location", ""),
    }
