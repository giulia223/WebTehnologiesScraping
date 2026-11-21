"""Name check (Task 4.5): one judged example for every core technology name.

Usage:
    python scripts/name_check.py sheet [--force]
    python scripts/name_check.py merge <verdicts.json> [<verdicts.json> ...] --reviewer TEXT
    python scripts/name_check.py spot-check --out <sample.json>
    python scripts/name_check.py merge --second <verdicts.json> --reviewer TEXT
    python scripts/name_check.py report

sheet       picks one example site per core name that has no detection on a labelled
            domain, and writes data/name_check.json. Verdicts already given for the same
            name and site are kept, so it can be re-run after a rule correction.
merge       copies reviewer verdicts into the sheet. Each file is a JSON list of
            {"name", "domain", "verdict": correct|wrong|unsure, "reason"}. With --second,
            they are the second reviewer's verdicts on the spot-check sample.
spot-check  writes the spot-check sample (every "wrong" verdict and a seeded sample of the
            others) for a second reviewer: name, site, sites count and evidence only, never
            the first reviewer's verdict, so the second judgement is blind.
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
    build_sheet,
    load_json,
    merge_second,
    merge_verdicts,
    render_report,
    spot_check_pick,
    summarise,
)

OUTPUT = Path("data/output")
GROUND_TRUTH = Path("data/ground_truth.json")
SHEET = Path("data/name_check.json")
REPORT = Path("docs/NAME_CHECK.md")
# What the second reviewer may see: no verdict, reason or reviewer of the first one.
BLIND_FIELDS = ("name", "domain", "sites", "categories", "confidence", "tier", "evidence")


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


def cmd_merge(paths: list[str], reviewer: str, second: bool) -> None:
    sheet = load_json(SHEET)
    merge = merge_second if second else merge_verdicts
    for path in paths:
        verdicts = json.loads(Path(path).read_text(encoding="utf-8"))
        print(f"{path}: {merge(sheet, verdicts, reviewer)} verdicts")
    save(sheet)
    left = sum(1 for e in sheet["names"] if not e["verdict"])
    print(f"not judged yet: {left}")


def cmd_spot_check(out: Path) -> None:
    sample = [{k: e[k] for k in BLIND_FIELDS} for e in spot_check_pick(load_json(SHEET))]
    out.write_text(json.dumps(sample, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(sample)} entries for the second reviewer -> {out}")


def cmd_report() -> None:
    lines = read_all_detections(OUTPUT)
    sheet = load_json(SHEET)
    summary = summarise(lines, load_json(GROUND_TRUTH), sheet)
    meta = load_json(OUTPUT / "summary.json")
    REPORT.write_text(render_report(summary, sheet, meta), encoding="utf-8")
    print(
        f"{REPORT}: {summary.names_with_correct} of {summary.core_names} core names "
        f"have a detection judged correct; second reviewer agreed on "
        f"{summary.agree}/{summary.spot_checked}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sheet = sub.add_parser("sheet")
    sheet.add_argument("--force", action="store_true")
    merge = sub.add_parser("merge")
    merge.add_argument("files", nargs="+")
    merge.add_argument("--reviewer", required=True)
    merge.add_argument("--second", action="store_true")
    spot = sub.add_parser("spot-check")
    spot.add_argument("--out", type=Path, required=True)
    sub.add_parser("report")
    args = parser.parse_args()
    if args.command == "sheet":
        cmd_sheet(args.force)
    elif args.command == "merge":
        cmd_merge(args.files, args.reviewer, args.second)
    elif args.command == "spot-check":
        cmd_spot_check(args.out)
    else:
        cmd_report()


if __name__ == "__main__":
    main()
