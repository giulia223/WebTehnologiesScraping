"""Precision, recall and F1 from the labels (Task 4.2)."""

from typing import Any

from techdetect.evaluate.metrics import (
    band_of,
    compute,
    expected_on_blind,
    f1,
    labelled_detections,
    rate,
    rate_if_enough,
    tally,
)
from techdetect.evaluate.metrics_report import render


def tech(name: str, confidence: int = 100, group: str = "core", **extra: Any) -> dict[str, Any]:
    evidence = [
        {
            "signal": "headers",
            "key": "server",
            "subkey": None,
            "pattern": "^nginx",
            "examples": [
                {
                    "source_artifact": "static.json",
                    "location": "https://a.com/",
                    "matched_value": "nginx",
                }
            ],
        }
    ]
    return {
        "name": name,
        "confidence": confidence,
        "group": group,
        "tier": "observed",
        "categories": ["Web servers"],
        "evidence": [] if extra.get("implied_by") else evidence,
        "implied_by": extra.get("implied_by"),
    }


def verdict(v: str, note: str = "") -> dict[str, str]:
    return {"verdict": v, "note": note}


def ground_truth() -> dict[str, Any]:
    return {
        "sets": {"blind": ["blind.com"], "review": ["review.com"]},
        "domains": {
            "blind.com": {
                "human_found": [
                    {"technology": "Nginx", "in_rules": True, "where": "Headers"},
                    {"technology": "jquery", "in_rules": True, "where": "Console"},  # any case
                    {"technology": "Own CMS", "in_rules": False, "where": "Footer"},
                    {"technology": "Cloudflare", "in_rules": True, "counts": False},
                    {"technology": "React", "in_rules": True, "after_seeing_tool": True},
                ],
                "verdicts": {"Nginx": verdict("correct"), "jQuery": verdict("correct")},
            },
            "review.com": {
                "verdicts": {
                    "Nginx": verdict("correct"),
                    "PayPal": verdict("wrong", "only a logo image"),
                    "MySQL": verdict("unsure"),
                    "Gone": verdict("correct"),  # detection no longer reported
                }
            },
        },
    }


def lines() -> dict[str, Any]:
    return {
        "blind.com": {"technologies": [tech("Nginx"), tech("jQuery", confidence=40)]},
        "review.com": {
            "technologies": [
                tech("Nginx"),
                tech("PayPal", confidence=50),
                tech("MySQL", group="inferred_only", implied_by="WordPress"),
                tech("Varnish"),  # no verdict yet
            ]
        },
    }


def test_rates_always_carry_their_counts() -> None:
    assert rate(119, 130) == "92% (119/130)"
    assert rate(0, 0) == "n/a (0/0)"
    assert rate_if_enough(4, 5) == "too few to measure (4/5)"
    assert rate_if_enough(9, 10) == "90% (9/10)"


def test_unsure_is_left_out_of_precision_and_thresholds_use_current_confidence() -> None:
    judged = labelled_detections(ground_truth(), lines()).judged
    at_0 = tally(judged)
    assert (at_0.correct, at_0.wrong, at_0.unsure, at_0.judged) == (3, 1, 1, 4)
    at_50 = tally(judged, 50)  # jQuery (40) drops out
    assert (at_50.correct, at_50.wrong) == (2, 1)
    at_80 = tally(judged, 80)  # PayPal (50) drops out too
    assert (at_80.correct, at_80.wrong) == (2, 0)


def test_unlabelled_and_stale_are_reported_not_hidden() -> None:
    labelled = labelled_detections(ground_truth(), lines())
    assert labelled.unlabelled == ["review.com: Varnish"]
    assert labelled.stale == ["review.com: Gone"]


def test_recall_skips_not_counted_and_after_tool_entries() -> None:
    expected = expected_on_blind(ground_truth(), lines())
    assert [(e.technology, e.found) for e in expected] == [
        ("Nginx", True),
        ("jquery", True),
        ("Own CMS", False),
    ]
    at_50 = expected_on_blind(ground_truth(), lines(), threshold=50)
    assert [e.found for e in at_50] == [True, False, False]


def test_f1_and_confidence_bands() -> None:
    assert f1(1.0, 0.5) == 2 / 3
    assert f1(None, 0.5) is None
    assert [band_of(c) for c in (100, 99, 75, 74, 50, 49, 0)] == [
        "100",
        "75-99",
        "75-99",
        "50-74",
        "50-74",
        "below 50",
        "below 50",
    ]


def test_headline_numbers_and_report_lists_every_false_positive() -> None:
    m = compute(ground_truth(), lines())
    at_0 = m["headline"][0]
    assert at_0["precision"] == [3, 4]
    assert at_0["recall"] == [2, 3]
    assert at_0["recall_with_rule"] == [2, 2]
    assert m["not_counted"][0]["technology"] == "Cloudflare"

    report = render(m)
    assert "75% (3/4)" in report
    assert "| review.com | PayPal | 50 | core | only a logo image |" in report
    assert "implied by WordPress" in report  # the unsure inferred detection
    assert "| blind.com | Own CMS | no rule |" in report
    assert "too few to measure" in report  # 4 judged detections per group at most
    assert "Warning:" in report


def test_platform_infrastructure_is_left_out_of_precision_and_listed() -> None:
    gt = ground_truth()
    gt["domains"]["review.com"]["verdicts"]["Cloudflare"] = verdict("wrong", "Wix's CDN")
    data = lines()
    data["review.com"]["technologies"].append(tech("Cloudflare", group="platform_infra"))

    m = compute(gt, data)
    assert m["headline"][0]["precision"] == [3, 4]  # unchanged: not a claim about the site
    assert m["platform_infra"] == [
        {"domain": "review.com", "technology": "Cloudflare", "verdict": "wrong"}
    ]
    assert "Left out of precision:** 1 detections" in render(m)
