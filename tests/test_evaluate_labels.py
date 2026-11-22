"""Ground-truth labelling: the sets, name matching, and the interactive session
(answers are typed through CliRunner's input). Uses a fake detections.jsonl."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from techdetect.cli import app
from techdetect.domains import Domain
from techdetect.evaluate.labels import blind_domains, match_name, pick_review

MINI_RULES = Path(__file__).parent / "fixtures" / "fingerprints" / "mini"
runner = CliRunner()
HOSTS = [f"site{n:02d}.com" for n in range(1, 21)]  # row 20 = site20.com is blind


def tech(name: str, confidence: int = 100) -> dict:
    return {
        "name": name,
        "group": "core",
        "tier": "observed",
        "confidence": confidence,
        "versions": [],
        "implied_by": None,
        "evidence": [
            {
                "signal": "html",
                "key": None,
                "subkey": None,
                "pattern": f"{name.lower()}-marker",
                "match_count": 1,
                "examples": [
                    {
                        "source_artifact": "static.html",
                        "location": "https://x/",
                        "matched_value": f"{name.lower()}-marker",
                        "context": "",
                    }
                ],
            }
        ],
    }


@pytest.fixture
def project(tmp_path: Path) -> dict[str, Path]:
    """An input list, a detections.jsonl and an empty ground-truth path."""
    domains = tmp_path / "domains.txt"
    domains.write_text("\n".join(HOSTS) + "\n", encoding="utf-8")
    out = tmp_path / "output"
    out.mkdir()
    lines = []
    for host in HOSTS:
        line = {"domain": host, "status": "ok", "redirected_to": None, "technologies": []}
        if host == "site03.com":
            line["status"] = "failed:dns_error"
        if host == "site04.com":
            line["redirected_to"] = "elsewhere.com"
        if host == "site05.com":
            line["technologies"] = [tech("Alpha"), tech("Delta", 50)]
        if host == "site20.com":
            line["technologies"] = [tech("Gamma")]
        lines.append(line)
    (out / "detections.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8"
    )
    return {"input": domains, "output": out, "gt": tmp_path / "ground_truth.json"}


def pick(project: dict[str, Path], *extra: str):
    return runner.invoke(
        app,
        [
            "evaluate",
            "pick-review",
            "--input",
            str(project["input"]),
            "--output-dir",
            str(project["output"]),
            "--ground-truth",
            str(project["gt"]),
            *extra,
        ],
    )


def label(project: dict[str, Path], domain: str, answers: str, *extra: str):
    return runner.invoke(
        app,
        [
            "evaluate",
            "label",
            domain,
            "--output-dir",
            str(project["output"]),
            "--ground-truth",
            str(project["gt"]),
            "--rules-dir",
            str(MINI_RULES),
            *extra,
        ],
        input=answers,
    )


def ground_truth(project: dict[str, Path]) -> dict:
    return json.loads(project["gt"].read_text(encoding="utf-8"))


def set_review(project: dict[str, Path], review: list[str]) -> None:
    data = ground_truth(project)
    data["sets"]["review"] = review
    project["gt"].write_text(json.dumps(data), encoding="utf-8")


# ------------------------------------------------------------ the sets


def test_blind_set_is_every_20th_row() -> None:
    domains = [Domain(row, f"d{row}.com", f"d{row}.com") for row in range(1, 201)]
    assert blind_domains(domains) == [f"d{row}.com" for row in range(20, 201, 20)]


def test_review_pick_is_reproducible_and_excludes_what_it_must() -> None:
    lines = [{"domain": h, "status": "ok", "redirected_to": None} for h in HOSTS]
    lines[2]["status"] = "failed:dns_error"
    lines[3]["redirected_to"] = "elsewhere.com"
    first = pick_review(lines, blind=["site20.com"], seed=7, size=5)

    assert first == pick_review(lines, blind=["site20.com"], seed=7, size=5)  # same seed
    assert len(first) == 5
    assert not {"site03.com", "site04.com", "site20.com"} & set(first)


def test_pick_review_command_saves_both_sets_once(project: dict[str, Path]) -> None:
    result = pick(project)
    assert result.exit_code == 0, result.output
    data = ground_truth(project)
    assert data["sets"]["blind"] == ["site20.com"]
    assert len(data["sets"]["review"]) == 10
    assert data["review_seed"] == 20261119

    again = pick(project)
    assert again.exit_code == 1  # an existing pick is never redrawn by accident
    assert "--force" in again.output


# ------------------------------------------------------------ names


def test_typed_names_are_matched_to_rule_names() -> None:
    names = ["Google Analytics", "Google Tag Manager", "Hotjar"]
    assert match_name("google analytics", names) == ("Google Analytics", [])
    exact, suggestions = match_name("Google Analitycs", names)
    assert exact is None and suggestions[0] == "Google Analytics"
    assert match_name("Something Else", names) == (None, [])


# ------------------------------------------------------------ the session


def test_review_domain_verdicts_and_missed_technologies(project: dict[str, Path]) -> None:
    pick(project)
    set_review(project, ["site05.com"])
    answers = (
        "\n".join(
            [
                "c",
                "",  # Alpha: correct, no note
                "w",
                "only in a comment",  # Delta: wrong, with a note
                "beta",
                "Network: beta.js",  # missed: matches the rule name "Beta"
                "Hotjarr",
                "Network: static.hotjar.com",  # missed: not in the rules, kept as typed
                "",  # done
            ]
        )
        + "\n"
    )
    result = label(project, "site05.com", answers)
    assert result.exit_code == 0, result.output

    record = ground_truth(project)["domains"]["site05.com"]
    assert record["verdicts"]["Alpha"]["verdict"] == "correct"
    assert record["verdicts"]["Delta"] == {
        **record["verdicts"]["Delta"],
        "verdict": "wrong",
        "note": "only in a comment",
        "found_by": "me",
        "confidence": 50,
    }
    assert [(m["technology"], m["in_rules"]) for m in record["missed"]] == [
        ("Beta", True),
        ("Hotjarr", False),
    ]


def test_quitting_keeps_the_answers_so_far_and_resumes(project: dict[str, Path]) -> None:
    pick(project)
    set_review(project, ["site05.com"])
    label(project, "site05.com", "c\n\nq\n")
    assert set(ground_truth(project)["domains"]["site05.com"]["verdicts"]) == {"Alpha"}

    # The next run asks only about what is still unlabelled.
    resumed = label(project, "site05.com", "u\n\n\n")
    assert "[1/2] Alpha" not in resumed.output
    assert (
        ground_truth(project)["domains"]["site05.com"]["verdicts"]["Delta"]["verdict"] == "unsure"
    )


def test_blind_domain_asks_for_the_human_list_before_showing_anything(
    project: dict[str, Path],
) -> None:
    pick(project)
    answers = (
        "\n".join(
            [
                "alpha",
                "Elements: generator tag",  # the human's own blind-inspection list
                "",  # list done: saved, and only now our detections appear
                "w",
                "",  # Gamma (ours): wrong
                "delta",
                "Console: window.Delta",  # missed, noticed after seeing our output
                "",
            ]
        )
        + "\n"
    )
    result = label(project, "site20.com", answers)
    assert result.exit_code == 0, result.output

    output = result.output
    assert output.index("BLIND") < output.index("=== Our detections")
    assert "Gamma" not in output[: output.index("=== Our detections")]  # nothing shown early
    record = ground_truth(project)["domains"]["site20.com"]
    assert [h["technology"] for h in record["human_found"]] == ["Alpha"]
    assert "after_seeing_tool" not in record["human_found"][0]
    # Found only after seeing our output: marked, so recall can leave it out.
    assert record["missed"][0]["after_seeing_tool"] is True


def test_domain_outside_both_sets_needs_confirmation(project: dict[str, Path]) -> None:
    pick(project)
    refused = label(project, "site06.com", "n\n")
    assert refused.exit_code == 1


def test_label_needs_the_sets_first(project: dict[str, Path]) -> None:
    result = label(project, "site05.com", "")
    assert result.exit_code == 1
    assert "pick-review" in result.output
