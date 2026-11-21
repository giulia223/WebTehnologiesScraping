"""Candidate rules: nothing reaches the results without a human `accepted` + promote."""

import json
import shutil
from pathlib import Path

import pytest
from raw_sites import Site
from typer.testing import CliRunner

from techdetect.cli import app
from techdetect.fingerprints.candidates import Candidate, problems, promote
from techdetect.fingerprints.loader import load_rules

MINI_RULES = Path(__file__).parent / "fixtures" / "fingerprints" / "mini"
runner = CliRunner()


def candidate(status: str, marker: str, **extra: object) -> dict:
    return {
        "cats": [2],
        "html": [marker],
        "status": status,
        "seen_on": 1,
        "examples": ["example-shop.com"],
        "source_url": "https://vendor.example/docs",
        "source_quote": f"Our snippet contains {marker}.",
        "source_verified": True,
        "origin": "data",
        **extra,
    }


@pytest.fixture
def rules_dir(tmp_path: Path) -> Path:
    root = tmp_path / "rules"
    shutil.copytree(MINI_RULES, root)
    (root / "candidates" / "proposed.json").unlink()
    rules = {
        "Proposed Tool": candidate("proposed", "proposed-marker"),
        "Accepted Tool": candidate("accepted", "accepted-marker"),
        "Rejected Tool": candidate("rejected", "rejected-marker"),
    }
    (root / "candidates" / "saas.json").write_text(json.dumps(rules), encoding="utf-8")
    return root


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------ promote


def test_only_accepted_rules_are_promoted(rules_dir: Path) -> None:
    moved = promote(rules_dir / "candidates", rules_dir / "custom")

    assert moved == ["Accepted Tool (saas.json)"]
    custom = read(rules_dir / "custom" / "saas.json")
    assert set(custom) == {"Accepted Tool"}
    # Proposed (not reviewed yet) and rejected stay where they are, never promoted.
    assert set(read(rules_dir / "candidates" / "saas.json")) == {"Proposed Tool", "Rejected Tool"}


def test_promoted_rules_carry_reviewed_by_human_and_a_date(rules_dir: Path) -> None:
    promote(rules_dir / "candidates", rules_dir / "custom")
    rule = read(rules_dir / "custom" / "saas.json")["Accepted Tool"]
    assert rule["reviewed_by"] == "human"
    assert len(rule["reviewed_at"]) == 10  # YYYY-MM-DD


def test_every_loaded_custom_rule_was_reviewed_by_a_human(rules_dir: Path) -> None:
    promote(rules_dir / "candidates", rules_dir / "custom")
    # A rule copied into custom/ by hand, without review, is not loaded.
    unreviewed = {"Sneaky Tool": candidate("accepted", "sneaky-marker")}
    (rules_dir / "custom" / "sneaky.json").write_text(json.dumps(unreviewed), encoding="utf-8")

    ruleset = load_rules(rules_dir)

    custom = {n for n, t in ruleset.technologies.items() if t.origin == "custom"}
    assert "Accepted Tool" in custom
    assert "Sneaky Tool" not in ruleset.technologies
    assert ruleset.report.unreviewed_custom == ["Sneaky Tool"]
    assert "NOT loaded (no reviewed_by: human): Sneaky Tool" in ruleset.report.format("v")


def test_promote_never_overwrites_an_existing_custom_rule(rules_dir: Path) -> None:
    existing = {"Accepted Tool": {"cats": [2], "html": ["old"], "reviewed_by": "human"}}
    (rules_dir / "custom" / "saas.json").write_text(json.dumps(existing), encoding="utf-8")
    with pytest.raises(ValueError, match="not overwritten"):
        promote(rules_dir / "candidates", rules_dir / "custom")
    assert read(rules_dir / "custom" / "saas.json") == existing


def test_promote_command(rules_dir: Path) -> None:
    result = runner.invoke(app, ["candidates", "promote", "--rules-dir", str(rules_dir)])
    assert result.exit_code == 0, result.output
    assert "promoted Accepted Tool (saas.json)" in result.output
    assert "'proposed': 1" in result.output and "'rejected': 1" in result.output


# ------------------------------------------------------------ review


def test_review_records_only_what_the_human_answers(rules_dir: Path, tmp_path: Path) -> None:
    candidates = {
        "Tool A": candidate("proposed", "tool-a-marker"),
        "Tool B": candidate("proposed", "tool-b-marker"),
        "Tool C": candidate("proposed", "tool-c-marker"),
    }
    (rules_dir / "candidates" / "saas.json").write_text(json.dumps(candidates), "utf-8")
    answers = "a\nvendor docs confirm it\nr\nmarker is too generic\ns\n"

    result = runner.invoke(
        app,
        ["candidates", "review", "--rules-dir", str(rules_dir), "--raw-dir", str(tmp_path / "raw")],
        input=answers,
    )
    assert result.exit_code == 0, result.output

    after = read(rules_dir / "candidates" / "saas.json")
    assert after["Tool A"]["status"] == "accepted"
    assert after["Tool A"]["review_note"] == "vendor docs confirm it"
    assert after["Tool B"]["status"] == "rejected"
    assert after["Tool C"]["status"] == "proposed"  # skipped: nothing is decided for the human


def test_review_shows_what_the_rule_matches_on_saved_data(rules_dir: Path, tmp_path: Path) -> None:
    page = tmp_path / "shop.html"
    page.write_text("<html><body><div class=proposed-marker>x</div></body></html>", "utf-8")
    Site(tmp_path / "raw", "example-shop.com").static(page)

    result = runner.invoke(
        app,
        ["candidates", "review", "--rules-dir", str(rules_dir), "--raw-dir", str(tmp_path / "raw")],
        input="q\n",
    )
    assert "example-shop.com: 1 matches" in result.output
    assert "'proposed-marker'" in result.output
    assert "checked: page fetched, quote found" in result.output


def test_unverified_source_is_shown_as_such(rules_dir: Path, tmp_path: Path) -> None:
    rules = {"Tool": candidate("proposed", "m", source_verified=False, origin="llm_suggested")}
    (rules_dir / "candidates" / "saas.json").write_text(json.dumps(rules), "utf-8")
    result = runner.invoke(
        app,
        ["candidates", "review", "--rules-dir", str(rules_dir), "--raw-dir", str(tmp_path)],
        input="q\n",
    )
    assert "NOT checked" in result.output
    assert "origin: llm_suggested" in result.output


def test_malformed_candidates_are_flagged(tmp_path: Path) -> None:
    rule = {"cats": [2], "html": ["x"], "status": "maybe", "examples": ["a", "b", "c"]}
    found = problems(Candidate(tmp_path / "x.json", "Bad", rule))
    assert "missing source_url" in found
    assert any("status must be one of" in p for p in found)
    assert "at most 2 examples" in found
