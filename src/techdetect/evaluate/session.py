"""`techdetect evaluate label <domain>`: label one domain by hand, in the terminal.

Blind domains first ask for the technologies the human found on their own (the blind inspection,
written down before the tool existed) and save them; only then is the tool's output shown,
so it cannot influence that list. Every answer is saved at once, so quitting midway
loses nothing and a later run continues where it stopped.
"""

from pathlib import Path
from typing import Any

import typer

from techdetect.evaluate.labels import (
    FOUND_BY,
    VERDICTS,
    domain_record,
    load_ground_truth,
    match_name,
    now,
    read_detections,
    save_ground_truth,
)

MAX_GROUPS_SHOWN = 4
MAX_CONTEXT_CHARS = 120


class Quit(Exception):
    """The human typed q: stop, everything answered so far is already saved."""


def ask_name(names: list[str]) -> tuple[str, bool] | None:
    """A technology name from the human, matched to the rule names. None = finished."""
    text = typer.prompt("  technology (empty = done)", default="", show_default=False).strip()
    if not text:
        return None
    exact, suggestions = match_name(text, names)
    if exact:
        typer.echo(f"    -> {exact}")
        return exact, True
    if suggestions:
        for number, name in enumerate(suggestions, start=1):
            typer.echo(f"    {number}. {name}")
        typer.echo(f'    0. keep "{text}" (not in the rules)')
        choice = typer.prompt("    which one", type=int, default=0)
        if 1 <= choice <= len(suggestions):
            return suggestions[choice - 1], True
    else:
        typer.echo("    (not in the rules: kept as typed)")
    return text, False


def ask_found_list(names: list[str], after_seeing_tool: bool) -> list[dict[str, Any]]:
    found = []
    while (answer := ask_name(names)) is not None:
        technology, in_rules = answer
        where = typer.prompt("  where did you see it (e.g. Network: static.hotjar.com)")
        entry = {
            "technology": technology,
            "in_rules": in_rules,
            "where": where,
            "found_by": FOUND_BY,
            "at": now(),
        }
        if after_seeing_tool:
            entry["after_seeing_tool"] = True
        found.append(entry)
    return found


def show_technology(index: int, total: int, tech: dict[str, Any]) -> None:
    versions = ", ".join(tech["versions"]) or "-"
    typer.echo(
        f"\n[{index}/{total}] {tech['name']}   group {tech['group']}, tier {tech['tier']}, "
        f"confidence {tech['confidence']}, versions {versions}"
    )
    if tech.get("implied_by"):
        typer.echo(f"    implied by {tech['implied_by']} (no direct evidence)")
    for group in tech["evidence"][:MAX_GROUPS_SHOWN]:
        name = " ".join(x for x in (group["key"], group["subkey"]) if x)
        typer.echo(
            f"    {group['signal']} {name}  x{group['match_count']}  rule: {group['pattern']!r}"
        )
        example = group["examples"][0]
        typer.echo(f"      {example['source_artifact']}  {example['location'] or ''}")
        typer.echo(f"      matched: {example['matched_value'][:MAX_CONTEXT_CHARS]!r}")
    hidden = len(tech["evidence"]) - MAX_GROUPS_SHOWN
    if hidden > 0:
        typer.echo(f"    ... and {hidden} more evidence groups")


def ask_verdict() -> tuple[str, str] | None:
    """(verdict, note), None to skip; raises Quit on q."""
    answer = typer.prompt("  [c]orrect / [w]rong / [u]nsure / [s]kip / [q]uit").strip().lower()
    if answer.startswith("q"):
        raise Quit
    verdict = {"c": "correct", "w": "wrong", "u": "unsure"}.get(answer[:1])
    if verdict is None:
        return None
    note = typer.prompt("  note (optional)", default="", show_default=False)
    return verdict, note


def label_domain(
    host: str, ground_truth_path: Path, output_dir: Path, names: list[str], relabel: bool = False
) -> None:
    data = load_ground_truth(ground_truth_path)
    sets = data["sets"]
    if host not in sets["blind"] and host not in sets["review"]:
        typer.echo(f"{host} is in neither the blind nor the review set.", err=True)
        if not typer.confirm("Label it anyway (kept as an extra)?", default=False):
            raise typer.Exit(code=1)
    record = domain_record(data, host)

    # Blind: the human's own list comes first and is saved before anything is shown.
    if host in sets["blind"] and not record["human_found"]:
        typer.echo(
            f"{host} is a BLIND domain. Enter every technology you confirmed "
            "in the blind inspection "
            "(docs/manual_inspect_domains.md), one at a time. The tool's results are shown "
            "only after this list is saved."
        )
        record["human_found"] = ask_found_list(names, after_seeing_tool=False)
        record["human_found_saved_at"] = now()
        save_ground_truth(ground_truth_path, data)
        typer.echo(f"saved {len(record['human_found'])} technologies found by hand.")

    line = read_detections(output_dir, host)
    if line is None:
        typer.echo(
            f"{host}: no line in {output_dir / 'detections.jsonl'}; run detect first.", err=True
        )
        raise typer.Exit(code=1)

    typer.echo(
        f"\n=== Our detections for {host}: status {line['status']}"
        + (f", redirected to {line['redirected_to']}" if line["redirected_to"] else "")
    )
    techs = line["technologies"]
    try:
        for index, tech in enumerate(techs, start=1):
            if tech["name"] in record["verdicts"] and not relabel:
                continue
            show_technology(index, len(techs), tech)
            answer = ask_verdict()
            if answer is None:
                continue
            verdict, note = answer
            assert verdict in VERDICTS
            record["verdicts"][tech["name"]] = {
                "verdict": verdict,
                "note": note,
                "found_by": FOUND_BY,
                "at": now(),
                "confidence": tech["confidence"],
                "group": tech["group"],
                "tier": tech["tier"],
            }
            save_ground_truth(ground_truth_path, data)

        typer.echo("\nTechnologies the tool MISSED on this site (seen by you in DevTools):")
        missed = ask_found_list(names, after_seeing_tool=host in sets["blind"])
        if missed:
            record["missed"].extend(missed)
            save_ground_truth(ground_truth_path, data)
    except Quit:
        typer.echo("stopped; every answer so far is saved.")
        return
    labelled = sum(1 for t in techs if t["name"] in record["verdicts"])
    typer.echo(
        f"{host}: {labelled}/{len(techs)} detections labelled, "
        f"{len(record['missed'])} missed technologies recorded."
    )
