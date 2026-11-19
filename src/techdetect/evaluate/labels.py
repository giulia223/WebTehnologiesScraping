"""Ground truth: what a human confirmed by hand on a sample of domains.

Two sets, kept in data/ground_truth.json:
  blind   every 20th input row (rows 20, 40, ..., 200), picked by position before the
          tool existed. The human lists every technology they can confirm in DevTools
          *before* seeing the tool's output: an honest measure of recall.
  review  10 domains drawn at random (fixed seed) from the domains the tool could read.
          The human marks each of the tool's detections correct / wrong / unsure:
          a measure of precision.

Every label records who made it (`found_by`), so a second opinion can be added later.
"""

import difflib
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from techdetect.collect.storage import write_json_atomic
from techdetect.domains import Domain

BLIND_EVERY = 20
REVIEW_SIZE = 10
REVIEW_SEED = 20261119  # fixed: anyone can re-draw the same review set
VERDICTS = ("correct", "wrong", "unsure")
FOUND_BY = "me"


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_ground_truth() -> dict[str, Any]:
    return {
        "format": 1,
        "blind_rule": f"every {BLIND_EVERY}th input row, picked by position "
        "before the tool existed",
        "review_rule": f"{REVIEW_SIZE} domains at random (seed {REVIEW_SEED}) among domains "
        "with status ok, not redirected elsewhere, not in the blind set",
        "sets": {"blind": [], "review": []},
        "domains": {},
    }


def load_ground_truth(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return empty_ground_truth()
    return json.loads(path.read_text(encoding="utf-8"))


def save_ground_truth(path: Path, data: dict[str, Any]) -> None:
    # Saved after every answer, atomically: quitting or a crash never loses labels.
    write_json_atomic(path, data)


def blind_domains(domains: list[Domain]) -> list[str]:
    """Rows 20, 40, ..., 200: chosen by position, not by content."""
    return [d.host for d in domains if d.row % BLIND_EVERY == 0]


def pick_review(
    lines: list[dict[str, Any]], blind: list[str], seed: int = REVIEW_SEED, size: int = REVIEW_SIZE
) -> list[str]:
    """Random domains the tool could read (status ok), not redirected to another site
    (their detections belong to that site), not in the blind set. Same seed, same pick."""
    candidates = [
        line["domain"]
        for line in lines
        if line["status"] == "ok" and not line.get("redirected_to") and line["domain"] not in blind
    ]
    return random.Random(seed).sample(candidates, min(size, len(candidates)))


def domain_record(data: dict[str, Any], host: str) -> dict[str, Any]:
    return data["domains"].setdefault(host, {"human_found": [], "verdicts": {}, "missed": []})


def match_name(text: str, names: list[str]) -> tuple[str | None, list[str]]:
    """A typed technology name -> (the rule name it means, or suggestions).

    "google analytics" is "Google Analytics". A near miss ("Google Analitycs") gets up to
    5 suggestions; the human picks one or keeps their own text (a technology the rules
    do not know, which matters when measuring recall).
    """
    text = text.strip()
    by_lower = {name.lower(): name for name in names}
    if text.lower() in by_lower:
        return by_lower[text.lower()], []
    close = difflib.get_close_matches(text.lower(), list(by_lower), n=5, cutoff=0.6)
    contains = [lower for lower in by_lower if text.lower() in lower and lower not in close]
    return None, [by_lower[lower] for lower in (close + contains)[:5]]


def read_detections(output_dir: Path, host: str) -> dict[str, Any] | None:
    """This domain's line of detections.jsonl, or None if detect has not seen it."""
    path = output_dir / "detections.jsonl"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        for text in f:
            line = json.loads(text)
            if line["domain"] == host:
                return line
    return None


def read_all_detections(output_dir: Path) -> list[dict[str, Any]]:
    path = output_dir / "detections.jsonl"
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines() if text]
