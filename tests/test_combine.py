"""Combining raw matches: one pattern counts once, confidence sum capped at 100, versions."""

import json
import shutil
from pathlib import Path

import pytest
from raw_sites import Site

from techdetect.fingerprints.combine import (
    MAX_EXAMPLES,
    combine,
    combine_confidence,
    plausible_version,
    resolve_version,
)
from techdetect.fingerprints.loader import load_rules
from techdetect.fingerprints.matcher import RuleIndex

FIXTURES = Path(__file__).parent / "fixtures"
MINI_RULES = FIXTURES / "fingerprints" / "mini"
REAL_RULES = Path(__file__).parents[1] / "fingerprints"

# Weak clues (confidence 50) in script URLs, written for these tests.
HINT_RULES = {
    "OneHint": {"cats": [1], "scriptSrc": ["one-hint\\.js\\;confidence:50"]},
    "TwoHints": {
        "cats": [1],
        "scriptSrc": ["two-a\\.js\\;confidence:50", "two-b\\.js\\;confidence:50"],
    },
    "Versioned": {"cats": [1], "scriptSrc": ["/lib-([\\d.]+)\\.js\\;version:\\1"]},
}


@pytest.fixture(scope="module")
def hint_index(tmp_path_factory: pytest.TempPathFactory) -> RuleIndex:
    rules_dir = tmp_path_factory.mktemp("rules") / "rules"
    shutil.copytree(MINI_RULES, rules_dir)
    (rules_dir / "custom" / "hints.json").write_text(
        json.dumps({n: {**r, "reviewed_by": "human"} for n, r in HINT_RULES.items()}),
        encoding="utf-8",
    )
    return RuleIndex(load_rules(rules_dir))


def page_with_scripts(folder: Path, *srcs: str) -> Path:
    page = folder / "scripts.html"
    tags = "".join(f'<script src="{src}"></script>' for src in srcs)
    page.write_text(f"<!doctype html><html><head>{tags}</head><body>x</body></html>", "utf-8")
    return page


# ------------------------------------------------------------ the formula


@pytest.mark.parametrize(
    ("confidences", "expected"),
    [([50], 50), ([50, 50], 100), ([25, 25, 50, 100], 100), ([25, 0], 25), ([], 0)],
)
def test_combine_confidence_is_a_sum_capped_at_100(confidences: list[int], expected: int) -> None:
    assert combine_confidence(confidences) == expected


# ------------------------------------------------------------ one pattern counts once (F3.3)


def test_one_weak_pattern_matching_16_times_still_counts_50(
    hint_index: RuleIndex, tmp_path: Path
) -> None:
    # 8 matching scripts on the plain page and the same 8 on the rendered page.
    page = page_with_scripts(tmp_path, *(f"/js/one-hint.js?part={i}" for i in range(8)))
    site = Site(tmp_path / "raw", "hints.example").static(page).browser(page)

    result = combine(site.match(hint_index).evidence)["OneHint"]

    assert result.confidence == 50  # not 100: it is still one clue
    [group] = result.groups
    assert group.match_count == 16
    assert group.artifacts == ("static.html", "rendered.html")
    assert len(group.examples) == MAX_EXAMPLES
    # Examples prefer different places: here 3 different script URLs.
    assert len({e.location for e in group.examples}) == MAX_EXAMPLES


def test_two_different_weak_patterns_add_up(hint_index: RuleIndex, tmp_path: Path) -> None:
    page = page_with_scripts(tmp_path, "/two-a.js", "/two-b.js", "/two-a.js?again=1")
    site = Site(tmp_path / "raw", "two.example").static(page)

    result = combine(site.match(hint_index).evidence)["TwoHints"]

    assert result.confidence == 100
    assert sorted(g.match_count for g in result.groups) == [1, 2]


# ------------------------------------------------------------ versions


@pytest.mark.parametrize(
    ("template", "full", "groups", "expected"),
    [
        ("\\1", "WordPress 6.6.2", ("6.6.2",), "6.6.2"),
        ("\\1?4:", "x", ("anything",), "4"),  # ternary: group matched
        ("\\1?4:", "x", (None,), ""),  # ternary: group did not match
        ("\\1?UA:GA4", "x", ("",), "GA4"),  # an empty group counts as "not matched"
        ("GA4", "_ga_ABC", (), "GA4"),  # a fixed version
        ("\\1", "x", ("1700000000123456",), ""),  # a group over 10 characters is not used
        ("\\1.\\2", "x", ("3", None), "3."),  # a group that did not match becomes empty
        ("", "x", ("1.0",), ""),  # no template, no version
    ],
)
def test_resolve_version(
    template: str, full: str, groups: tuple[str | None, ...], expected: str
) -> None:
    assert resolve_version(template, full, groups) == expected


@pytest.mark.parametrize(
    ("version", "ok"),
    [
        ("3.6.0", True),
        ("GA4", True),
        ("1700000000", False),
        ("1.2.3.4.5.6.7.8.9", False),
        ("", False),
    ],
)
def test_plausible_version(version: str, ok: bool) -> None:
    assert plausible_version(version) is ok


def test_every_distinct_version_is_kept(hint_index: RuleIndex, tmp_path: Path) -> None:
    page = page_with_scripts(tmp_path, "/lib-1.12.4.js", "/lib-3.6.0.js", "/lib-3.6.0.js")
    site = Site(tmp_path / "raw", "versions.example").static(page)

    result = combine(site.match(hint_index).evidence)["Versioned"]

    assert result.versions == ("1.12.4", "3.6.0")  # no "main" version is picked


# ------------------------------------------------------------ with the real rules


def test_real_wordpress_page(tmp_path: Path) -> None:
    index = RuleIndex(load_rules(REAL_RULES))
    site = Site(tmp_path, "wordpress.example").static("wordpress.html")

    results = combine(site.match(index).evidence)

    wordpress = results["WordPress"]
    assert wordpress.confidence == 100
    assert wordpress.versions == ("6.6.2",)
    assert wordpress.tier == "observed"  # html and scripts, not only the generator tag
    assert {g.signal for g in wordpress.groups} >= {"meta", "html", "scriptSrc"}
