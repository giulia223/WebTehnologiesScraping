"""Name check: one judged example per core technology name."""

from typing import Any

import pytest

from techdetect.evaluate.name_check import (
    BATCH_SIZE,
    build_sheet,
    core_examples,
    final_verdict,
    labelled_verdicts,
    merge_second,
    merge_verdicts,
    pick_domain,
    render_report,
    spot_check_pick,
    summarise,
)


def tech(name: str, group: str = "core") -> dict[str, Any]:
    return {
        "name": name,
        "group": group,
        "categories": ["Analytics"],
        "confidence": 100,
        "tier": "observed",
        "versions": [],
        "evidence": [
            {
                "signal": "scriptSrc",
                "key": None,
                "pattern": "hotjar",
                "examples": [{"source_artifact": "static.html", "matched_value": "hotjar"}],
            }
        ],
    }


def line(domain: str, *techs: dict[str, Any], status: str = "ok", **extra: Any) -> dict:
    return {
        "domain": domain,
        "status": status,
        "redirected_to": extra.get("redirected_to"),
        "redirect_same_name": extra.get("redirect_same_name", False),
        "technologies": list(techs),
    }


def ground_truth(verdicts: dict[str, dict[str, str]]) -> dict[str, Any]:
    return {
        "domains": {
            d: {"verdicts": {n: {"verdict": v} for n, v in names.items()}}
            for d, names in verdicts.items()
        }
    }


LINES = [
    line("lab.com", tech("jQuery"), tech("PHP", group="inferred_only")),
    line("a.com", tech("jQuery"), tech("Hotjar"), tech("PHP")),
    line("b.com", tech("Hotjar"), tech("Criteo")),
    line("c.com", tech("Hotjar")),
    line("park.com", tech("Sedo"), status="parked"),
    line("moved.com", tech("Other"), redirected_to="else.com"),
]
GT = ground_truth({"lab.com": {"jQuery": "correct", "PHP": "correct"}})


def test_same_seed_same_pick_on_two_runs() -> None:
    first = build_sheet(LINES, GT)
    second = build_sheet(LINES, GT)
    assert [(e["name"], e["domain"]) for e in first["names"]] == [
        (e["name"], e["domain"]) for e in second["names"]
    ]


def test_a_names_pick_does_not_depend_on_other_names() -> None:
    sites = ["a.com", "b.com", "c.com", "d.com"]
    assert pick_domain("Hotjar", sites) == pick_domain("Hotjar", list(reversed(sites)))


def test_only_core_names_without_a_labelled_verdict_are_listed() -> None:
    names = [e["name"] for e in build_sheet(LINES, GT)["names"]]
    # jQuery and PHP were judged on lab.com (PHP in another group still counts as judged);
    # Sedo is only on a parked page and Other only on a redirect: not core names.
    assert names == ["Criteo", "Hotjar"]
    hotjar = next(e for e in build_sheet(LINES, GT)["names"] if e["name"] == "Hotjar")
    assert hotjar["sites"] == 3 and hotjar["domain"] in {"a.com", "b.com", "c.com"}


def test_core_names_follow_the_summary_counting_rule() -> None:
    same_company = line("x.eu", tech("Klaviyo"), redirected_to="x.com", redirect_same_name=True)
    assert set(core_examples(LINES + [same_company])) == {
        "jQuery",
        "PHP",
        "Hotjar",
        "Criteo",
        "Klaviyo",
    }


def test_a_verdict_below_the_threshold_does_not_cover_a_name() -> None:
    # Criteo judged on lab.com, but detected there only at confidence 0: not counted.
    lines = [
        {**ln, "technologies": [*ln["technologies"], {**tech("Criteo"), "confidence": 0}]}
        if ln["domain"] == "lab.com"
        else ln
        for ln in LINES
    ]
    gt = ground_truth({"lab.com": {"jQuery": "correct", "PHP": "correct", "Criteo": "correct"}})
    assert "Criteo" not in labelled_verdicts(gt, lines)
    assert "Criteo" in [e["name"] for e in build_sheet(lines, gt)["names"]]


def test_a_stale_verdict_does_not_cover_a_name() -> None:
    # lab.com was judged on Criteo once, but no longer detects it.
    gt = ground_truth({"lab.com": {"jQuery": "correct", "PHP": "correct", "Criteo": "correct"}})
    assert "Criteo" in [e["name"] for e in build_sheet(LINES, gt)["names"]]


def test_verdicts_are_kept_when_the_sheet_is_rebuilt() -> None:
    sheet = build_sheet(LINES, GT)
    criteo = sheet["names"][0]
    merge_verdicts(sheet, [{**criteo, "verdict": "wrong", "reason": "x"}], "reviewer 1")
    rebuilt = build_sheet(LINES, GT, previous=sheet)
    assert rebuilt["names"][0]["verdict"] == "wrong"
    assert rebuilt["names"][0]["reviewer"] == "reviewer 1"


def test_a_verdict_whose_example_detection_is_gone_counts_as_not_judged() -> None:
    sheet = build_sheet(LINES, GT)
    hotjar = next(e for e in sheet["names"] if e["name"] == "Hotjar")
    hotjar.update(verdict="wrong", reason="x")
    # A correction removed Hotjar from the example site; it is still on the other two.
    lines = [
        {**ln, "technologies": [t for t in ln["technologies"] if t["name"] != "Hotjar"]}
        if ln["domain"] == hotjar["domain"]
        else ln
        for ln in LINES
    ]
    summary = summarise(lines, GT, sheet)
    assert summary.checked["wrong"] == 0
    assert summary.checked["none"] == 2  # Hotjar (stale) and Criteo (never judged)
    assert summary.example_gone == ["Hotjar"]
    text = render_report(summary, sheet, {"tool_version": "t", "ruleset_version": "r"})
    assert "## Judged wrong (0)" in text  # an old verdict is not listed as a current one
    assert "## Not judged yet (2)" in text and "Hotjar (was wrong on" in text  # + Criteo
    rebuilt = build_sheet(lines, GT, previous=sheet)
    new_hotjar = next(e for e in rebuilt["names"] if e["name"] == "Hotjar")
    assert new_hotjar["domain"] != hotjar["domain"] and new_hotjar["verdict"] is None


def test_a_correct_name_that_leaves_core_is_listed_not_counted() -> None:
    # Like Duda: judged correct, then no longer counted (all its clues worth 0).
    sheet = build_sheet(LINES, GT)
    criteo = next(e for e in sheet["names"] if e["name"] == "Criteo")
    criteo.update(verdict="correct", reason="script loaded")
    lines = [
        {
            **ln,
            "technologies": [
                {**t, "confidence": 0} if t["name"] == "Criteo" else t for t in ln["technologies"]
            ],
        }
        for ln in LINES
    ]
    summary = summarise(lines, GT, sheet)
    assert summary.gone == ["Criteo"]
    assert summary.checked["correct"] == 0
    text = render_report(summary, sheet, {"tool_version": "t", "ruleset_version": "r"})
    assert "## No longer core (1)" in text and "Criteo" in text


def test_a_sheet_name_later_judged_on_a_labelled_domain_is_not_counted_twice() -> None:
    sheet = build_sheet(LINES, GT)
    hotjar = next(e for e in sheet["names"] if e["name"] == "Hotjar")
    hotjar.update(verdict="correct", reason="script loaded")
    lab = next(ln for ln in LINES if ln["domain"] == "lab.com")
    lines = [
        {**ln, "technologies": [*ln["technologies"], tech("Hotjar")]} if ln is lab else ln
        for ln in LINES
    ]
    gt = ground_truth({"lab.com": {"jQuery": "correct", "PHP": "correct", "Hotjar": "correct"}})
    summary = summarise(lines, gt, sheet)
    assert summary.now_labelled == ["Hotjar"]
    assert summary.checked["correct"] == 0  # counted once, on the labelled domain
    assert summary.names_with_correct == 3  # jQuery, PHP, Hotjar


def test_merge_refuses_bad_verdicts_and_other_sites() -> None:
    sheet = build_sheet(LINES, GT)
    with pytest.raises(ValueError):
        merge_verdicts(sheet, [{"name": "Criteo", "domain": "b.com", "verdict": "maybe"}], "r")
    with pytest.raises(ValueError):
        merge_verdicts(sheet, [{"name": "Criteo", "domain": "b.com", "verdict": "wrong"}], "r")
    with pytest.raises(ValueError):
        merge_verdicts(
            sheet, [{"name": "Criteo", "domain": "z.com", "verdict": "wrong", "reason": "x"}], "r"
        )


def test_batches_have_at_most_batch_size_names() -> None:
    lines = [line(f"s{i}.com", tech(f"Tech {i:03}")) for i in range(BATCH_SIZE + 5)]
    sheet = build_sheet(lines, ground_truth({}))
    assert [e["batch"] for e in sheet["names"]].count(1) == BATCH_SIZE
    assert [e["batch"] for e in sheet["names"]].count(2) == 5


def test_spot_check_takes_every_wrong_plus_a_sample() -> None:
    lines = [line(f"s{i}.com", tech(f"Tech {i:02}")) for i in range(30)]
    sheet = build_sheet(lines, ground_truth({}))
    for i, entry in enumerate(sheet["names"]):
        entry["verdict"] = "wrong" if i < 3 else "correct"
    picks = spot_check_pick(sheet)
    assert len(picks) == 3 + 10
    assert [p["verdict"] for p in picks[:3]] == ["wrong"] * 3
    assert picks == spot_check_pick(sheet)  # seeded: the same sample every time


def test_report_headline_counts_names_with_a_correct_detection() -> None:
    sheet = build_sheet(LINES, GT)
    sheet["names"][0].update(verdict="wrong", reason="a mention", second_verdict="wrong")
    sheet["names"][1].update(verdict="correct", reason="script loaded")
    summary = summarise(LINES, GT, sheet)
    # jQuery and PHP (labelled) + Hotjar (checked) = 3 of 4 core names.
    assert summary.names_with_correct == 3
    assert summary.core_names == 4
    assert (summary.agree, summary.spot_checked) == (1, 1)
    text = render_report(summary, sheet, {"tool_version": "t", "ruleset_version": "r"})
    assert "**3 of 4 core names have at least one detection judged correct.**" in text
    assert "## Judged wrong (1)" in text
    assert "Both reviewers are agents." in text  # the report never presents agents as a human
    assert "no human" not in text


def test_a_disagreement_between_reviewers_counts_as_unsure() -> None:
    sheet = build_sheet(LINES, GT)
    criteo, hotjar = sheet["names"]
    criteo.update(verdict="wrong", reason="a mention")
    hotjar.update(verdict="correct", reason="script loaded")
    merge_second(
        sheet,
        [
            {"name": "Criteo", "domain": criteo["domain"], "verdict": "wrong", "reason": "x"},
            {"name": "Hotjar", "domain": hotjar["domain"], "verdict": "unsure", "reason": "y"},
        ],
        "second reviewer",
    )
    assert final_verdict(criteo) == "wrong"  # agreed
    assert final_verdict(hotjar) == "unsure"  # disagreed: can only lower the headline
    summary = summarise(LINES, GT, sheet)
    assert summary.names_with_correct == 2  # Hotjar no longer counts as correct
    assert (summary.agree, summary.spot_checked) == (1, 2)
    rebuilt = build_sheet(LINES, GT, previous=sheet)  # second verdicts survive a rebuild
    assert [e["second_verdict"] for e in rebuilt["names"]] == ["wrong", "unsure"]
