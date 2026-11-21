"""Name check (Task 4.5): one judged example for every core technology name.

Usage:
    python scripts/name_check.py sheet [--force]
    python scripts/name_check.py merge <verdicts.json> [<verdicts.json> ...] --reviewer TEXT
    python scripts/name_check.py spot-check
    python scripts/name_check.py report

sheet       picks one example site per core name that has no detection on a labelled
            domain, and writes data/name_check.json. Verdicts already given for the same
            name and site are kept, so it can be re-run after a rule correction.
merge       copies reviewer verdicts into the sheet. Each file is a JSON list of
            {"name", "domain", "verdict": correct|wrong|unsure, "reason"}.
spot-check  the human re-judges every "wrong" verdict and a seeded sample of the others,
            in the terminal. Answers are saved one by one; quitting loses nothing.
report      writes docs/NAME_CHECK.md (generated, do not edit by hand).

Offline: reads data/output/detections.jsonl, data/output/summary.json and
data/ground_truth.json. Judging is never done by this script.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from techdetect.evaluate.labels import read_all_detections  # noqa: E402
from techdetect.evaluate.name_check import (  # noqa: E402
    VERDICTS,
    build_sheet,
    evidence_line,
    load_json,
    merge_verdicts,
    render_report,
    spot_check_pick,
    summarise,
)

OUTPUT = Path("data/output")
GROUND_TRUTH = Path("data/ground_truth.json")
SHEET = Path("data/name_check.json")
REPORT = Path("docs/NAME_CHECK.md")


def save(sheet: dict) -> None:
    SHEET.write_text(json.dumps(sheet, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def cmd_sheet(force: bool) -> None:
    previous = load_json(SHEET) if SHEET.exists() else None
    if previous and not force and any(e["verdict"] for e in previous["names"]):
        # Verdicts for the same name and site are carried over, but say it out loud.
        print(f"{SHEET} has verdicts; re-run with --force to rebuild (they are kept per site)")
        return
    sheet = build_sheet(read_all_detections(OUTPUT), load_json(GROUND_TRUTH), previous)
    save(sheet)
    names = sheet["names"]
    batches = max((e["batch"] for e in names), default=0)
    kept = sum(1 for e in names if e["verdict"])
    print(f"{len(names)} names to check in {batches} batches -> {SHEET} ({kept} verdicts kept)")


def cmd_merge(paths: list[str], reviewer: str) -> None:
    sheet = load_json(SHEET)
    for path in paths:
        verdicts = json.loads(Path(path).read_text(encoding="utf-8"))
        print(f"{path}: {merge_verdicts(sheet, verdicts, reviewer)} verdicts")
    save(sheet)
    left = sum(1 for e in sheet["names"] if not e["verdict"])
    print(f"not judged yet: {left}")


def cmd_spot_check() -> None:
    sheet = load_json(SHEET)
    picks = [e for e in spot_check_pick(sheet) if not e.get("human_verdict")]
    print(f"{len(picks)} to re-judge (c = correct, w = wrong, u = unsure, q = quit)")
    for i, entry in enumerate(picks, 1):
        print(f"\n[{i}/{len(picks)}] {entry['name']} on {entry['domain']} ({entry['sites']} sites)")
        print(f"  evidence: {evidence_line(entry)}")
        for e in entry["evidence"][:3]:
            for x in e.get("examples", [])[:1]:
                print(f"    {e['signal']} at {x.get('location')}: {x.get('context', '')[:160]}")
        print(f"  reviewer said: {entry['verdict']}: {entry['reason']}")
        answer = ""
        while answer not in ("c", "w", "u", "q"):
            answer = input("  your verdict [c/w/u/q]: ").strip().lower()
        if answer == "q":
            break
        entry["human_verdict"] = dict(zip("cwu", VERDICTS, strict=True))[answer]
        entry["human_note"] = input("  note (optional): ").strip() or None
        save(sheet)  # after every answer, so quitting loses nothing


def cmd_report() -> None:
    lines = read_all_detections(OUTPUT)
    sheet = load_json(SHEET)
    summary = summarise(lines, load_json(GROUND_TRUTH), sheet)
    meta = load_json(OUTPUT / "summary.json")
    REPORT.write_text(render_report(summary, sheet, meta), encoding="utf-8")
    print(
        f"{REPORT}: {summary.names_with_correct} of {summary.core_names} core names "
        f"have a detection judged correct"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sheet = sub.add_parser("sheet")
    sheet.add_argument("--force", action="store_true")
    merge = sub.add_parser("merge")
    merge.add_argument("files", nargs="+")
    merge.add_argument("--reviewer", required=True)
    sub.add_parser("spot-check")
    sub.add_parser("report")
    args = parser.parse_args()
    if args.command == "sheet":
        cmd_sheet(args.force)
    elif args.command == "merge":
        cmd_merge(args.files, args.reviewer)
    elif args.command == "spot-check":
        cmd_spot_check()
    else:
        cmd_report()


if __name__ == "__main__":
    main()
