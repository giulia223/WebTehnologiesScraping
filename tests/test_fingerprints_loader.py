"""Fingerprint loader tests.

Most tests use a small hand-written rule set in webappanalyzer's format
(tests/fixtures/fingerprints/mini), so they keep passing when the real rules are
updated. One group loads the real pinned copy and checks three real technologies.
"""

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from techdetect.cli import app
from techdetect.fingerprints.loader import RuleSet, js_regex_to_python, load_rules, split_tags

MINI = Path(__file__).parent / "fixtures" / "fingerprints" / "mini"
REAL = Path(__file__).parents[1] / "fingerprints"


@pytest.fixture(scope="module")
def mini() -> RuleSet:
    return load_rules(MINI)


def one(ruleset: RuleSet, tech: str, field: str, key: str | None = None, subkey: str | None = None):
    found = [
        p
        for p in ruleset.technologies[tech].patterns
        if p.field == field and p.key == key and (subkey is None or p.subkey == subkey)
    ]
    assert len(found) == 1, found
    return found[0]


# ------------------------------------------------------------ pattern syntax


def test_tags_split_on_first_colon_so_ternary_versions_stay_whole() -> None:
    text, attrs = split_tags("gamma\\.css\\?v=([\\d.]+)\\;version:\\1?\\1:old\\;confidence:25")
    assert text == "gamma\\.css\\?v=([\\d.]+)"
    assert attrs == {"version": "\\1?\\1:old", "confidence": "25"}


def test_untagged_pattern_has_confidence_100_and_no_version(mini: RuleSet) -> None:
    pattern = one(mini, "Beta", "scriptSrc")
    assert pattern.confidence == 100
    assert pattern.version == ""
    assert pattern.source == "beta\\.js"


def test_regexes_are_case_insensitive(mini: RuleSet) -> None:
    assert one(mini, "Beta", "scriptSrc").regex.search("/static/BETA.JS")


def test_version_and_confidence_tags(mini: RuleSet) -> None:
    header = one(mini, "Alpha", "headers", "x-powered-by")
    assert header.version == "\\1"
    assert header.regex.search("Alpha/2.4").group(1) == "2.4"
    assert one(mini, "Alpha", "meta", "generator").confidence == 50


def test_names_are_lowercased_except_js_chains(mini: RuleSet) -> None:
    # As in Wappalyzer: header, cookie and meta names are matched case-insensitively,
    # JavaScript property chains are not (window.Alpha differs from window.alpha).
    assert one(mini, "Alpha", "cookies", "alpha_sid").source == ""
    assert one(mini, "Alpha", "js", "Alpha.Version").version == "\\1"


def test_dns_patterns_by_record_type(mini: RuleSet) -> None:
    dns = [p for p in mini.technologies["Beta"].patterns if p.field == "dns"]
    assert sorted((p.key, p.source) for p in dns) == [
        ("mx", "beta-mail\\.example"),
        ("txt", "beta-verify="),
        ("txt", "v=spf1 .*include:beta\\.example"),
    ]


def test_dom_selector_list_means_the_element_exists(mini: RuleSet) -> None:
    dom = [p for p in mini.technologies["Beta"].patterns if p.field == "dom"]
    assert {(p.key, p.subkey, p.source) for p in dom} == {
        ("div.beta-widget", "exists", ""),
        ("script[data-beta]", "exists", ""),
    }


def test_dom_object_with_attribute_and_text(mini: RuleSet) -> None:
    selector = "link[href*='gamma']"
    href = one(mini, "Gamma", "dom", selector, "attributes.href")
    assert href.confidence == 25
    assert href.version == "\\1?\\1:old"
    assert one(mini, "Gamma", "dom", selector, "text").source == "Gamma"


def test_relations(mini: RuleSet) -> None:
    alpha = mini.technologies["Alpha"]
    assert [(i.name, i.confidence) for i in alpha.implies] == [("Beta", 50), ("Gamma", 100)]
    gamma = mini.technologies["Gamma"]
    assert gamma.requires == ("Alpha",)
    assert gamma.requires_category == (1,)
    assert mini.technologies["Beta"].excludes == ("Gamma",)
    assert one(mini, "Gamma", "certIssuer").source == "Gamma CA"


@pytest.mark.parametrize(
    ("js", "python", "changed"),
    [
        ("\\APC\\b", "APC\\b", 1),  # JS "\A" is the letter A; Python's is "start of text"
        ("openpay\\.com.\\au", "openpay\\.com.au", 1),  # JS "\a" is "a"; Python's is a bell
        ("\\d+\\.\\w\\s\\b\\x41\\u0041", "\\d+\\.\\w\\s\\b\\x41\\u0041", 0),  # real escapes
        ("a\\\\Ab", "a\\\\Ab", 0),  # an escaped backslash followed by A stays as it is
    ],
)
def test_javascript_only_escapes_are_rewritten(js: str, python: str, changed: int) -> None:
    assert js_regex_to_python(js) == (python, changed)


# ------------------------------------------------------------ problems are reported


def test_unusable_patterns_are_skipped_and_reported(mini: RuleSet) -> None:
    reasons = {(s.technology, s.pattern): s.reason for s in mini.report.skipped}
    assert reasons[("Broken", "(unclosed")].startswith("invalid regex")
    assert reasons[("Broken", "a\\;confidence:high")].startswith("confidence is not a number")
    # The rest of the technology still loads.
    assert one(mini, "Broken", "html").source == "fine"


def test_dangling_references_and_unused_fields_are_reported(mini: RuleSet) -> None:
    report = mini.report
    assert ("Broken", "implies", "Nobody") in report.unknown_relations
    assert ("Broken", 99) in report.unknown_categories
    assert report.ignored_fields == {"probe": 1}  # description, website...: metadata


def test_report_counts_patterns_per_confidence(mini: RuleSet) -> None:
    text = mini.report.format(mini.version)
    assert "patterns per confidence: 100: " in text
    assert " | 50: 1 | 25: 1" in text
    assert "skipped patterns: 2" in text
    assert mini.report.pattern_count == sum(mini.report.patterns_by_confidence.values())


# ------------------------------------------------------------ custom and candidates


def test_custom_rules_add_to_known_technologies_and_create_new_ones(mini: RuleSet) -> None:
    alpha = mini.technologies["Alpha"]
    assert alpha.origin == "webappanalyzer"  # extended, not replaced
    assert alpha.categories == (1, 2)
    assert {p.source for p in alpha.patterns if p.field == "html"} == {
        '<div id="alpha-root"',
        "alpha-custom-marker",
    }
    assert mini.technologies["Delta"].origin == "custom"
    assert mini.report.merged == ["Alpha (custom)"]


def test_candidates_load_only_on_request_and_never_rejected(mini: RuleSet) -> None:
    assert "Epsilon" not in mini.technologies

    with_candidates = load_rules(MINI, include_candidates=True)
    names = {n for n, t in with_candidates.technologies.items() if t.origin == "candidates"}
    assert names == {"Epsilon", "Zeta"}
    assert with_candidates.report.candidates_left_out == {"rejected": 1, "None": 1}
    assert "+candidates:" in with_candidates.version


def test_version_names_the_commit_and_hashes_custom_rules_by_content(
    mini: RuleSet, tmp_path: Path
) -> None:
    assert mini.version.startswith("webappanalyzer@0123456789ab+custom:")

    # The same rules with other formatting and key order give the same version.
    copy = tmp_path / "rules"
    shutil.copytree(MINI, copy)
    custom = json.loads((copy / "custom" / "extra.json").read_text(encoding="utf-8"))
    reordered = dict(reversed(list(custom.items())))
    (copy / "custom" / "extra.json").write_text(json.dumps(reordered, indent=4), encoding="utf-8")
    assert load_rules(copy).version == mini.version

    # A changed rule gives a new version.
    custom["Delta"]["headers"]["X-Delta"] = "v2"
    (copy / "custom" / "extra.json").write_text(json.dumps(custom), encoding="utf-8")
    assert load_rules(copy).version != mini.version


def test_broken_custom_file_stops_loading(tmp_path: Path) -> None:
    copy = tmp_path / "rules"
    shutil.copytree(MINI, copy)
    (copy / "custom" / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="bad.json"):
        load_rules(copy)


def test_rules_command_prints_the_report() -> None:
    result = CliRunner().invoke(app, ["rules", "--rules-dir", str(MINI)])
    assert result.exit_code == 0, result.output
    assert "technologies: 5 (entries read: webappanalyzer 4, custom 2)" in result.output
    assert "candidates not loaded" in result.output


# ------------------------------------------------------------ the real rules


@pytest.fixture(scope="module")
def real() -> RuleSet:
    return load_rules(REAL)


def test_real_rules_load_at_the_pinned_commit(real: RuleSet) -> None:
    assert real.report.upstream["commit"] == "9e52f10a9533c8f7f8d0703c843f7295d56c95a3"
    assert real.version.startswith("webappanalyzer@9e52f10a9533+custom:none+corrections:")
    assert len(real.technologies) > 7000
    assert real.categories[1].name == "CMS"


def test_real_wordpress(real: RuleSet) -> None:
    wordpress = real.technologies["WordPress"]
    assert {i.name for i in wordpress.implies} >= {"PHP", "MySQL"}
    generator = one(real, "WordPress", "meta", "generator")
    assert generator.version == "\\1"
    assert generator.confidence == 100
    assert generator.regex.search("WordPress 6.6.2").group(1) == "6.6.2"


def test_real_jquery(real: RuleSet) -> None:
    chains = {p.key for p in real.technologies["jQuery"].patterns if p.field == "js"}
    assert "jQuery.fn.jquery" in chains  # case kept
    versioned = [
        p
        for p in real.technologies["jQuery"].patterns
        if p.field == "scriptSrc" and p.version == "\\1"
    ]
    url = "https://code.jquery.com/jquery-3.6.0.min.js"
    assert any((m := p.regex.search(url)) and m.group(1) == "3.6.0" for p in versioned)


def test_real_shopify(real: RuleSet) -> None:
    assert one(real, "Shopify", "js", "Shopify").confidence == 25
    href = [p for p in real.technologies["Shopify"].patterns if p.subkey == "attributes.href"]
    assert [p.confidence for p in href] == [50]
    cookies = {p.key for p in real.technologies["Shopify"].patterns if p.field == "cookies"}
    assert "_shopify_y" in cookies


# ------------------------------------------------------------ corrections


def write_corrections(rules: Path, corrections: dict) -> None:
    folder = rules / "corrections"
    folder.mkdir(exist_ok=True)
    (folder / "corrections.json").write_text(json.dumps(corrections), encoding="utf-8")


def test_reviewed_corrections_remove_one_pattern_or_link(mini: RuleSet, tmp_path: Path) -> None:
    copy = tmp_path / "rules"
    shutil.copytree(MINI, copy)
    write_corrections(
        copy,
        {
            "Alpha": {
                "remove": [{"field": "cookies", "key": "ALPHA_SID", "pattern": ""}],
                "reason": "a cookie another product sets too",
                "reviewed_by": "human",
            },
            "Broken": {"remove_implies": ["Nobody"], "reviewed_by": "human"},
            "Beta": {"remove": [{"field": "scriptSrc", "pattern": "beta\.js"}]},  # unreviewed
        },
    )
    corrected = load_rules(copy)

    alpha = corrected.technologies["Alpha"]
    assert [p.field for p in alpha.patterns if p.field == "cookies"] == []
    assert len(alpha.patterns) == len(mini.technologies["Alpha"].patterns) - 1
    assert corrected.technologies["Broken"].implies == ()
    # Not reviewed by a person: listed, not applied.
    sources = [p.source for p in corrected.technologies["Beta"].patterns]
    assert sources == [p.source for p in mini.technologies["Beta"].patterns]
    assert corrected.report.unreviewed_corrections == ["Beta"]
    assert len(corrected.report.corrections_applied) == 2
    assert "+corrections:none" not in corrected.version
    assert "+corrections:none" in mini.version


def test_a_correction_that_matches_nothing_is_reported(tmp_path: Path) -> None:
    copy = tmp_path / "rules"
    shutil.copytree(MINI, copy)
    write_corrections(
        copy,
        {
            "Alpha": {
                "remove": [{"field": "cookies", "key": "OTHER", "pattern": ""}],
                "reviewed_by": "human",
            },
            "Nowhere": {"remove_implies": ["Alpha"], "reviewed_by": "human"},
        },
    )
    report = load_rules(copy).report
    assert report.corrections_applied == []
    assert report.corrections_not_applied == [
        "Alpha: cookies OTHER '' (no such pattern)",
        "Nowhere: no such technology",
    ]


def test_every_real_correction_matches_the_pinned_rules(tmp_path: Path) -> None:
    # If upstream changes a pattern we correct, this fails instead of silently doing nothing.
    copy = tmp_path / "rules"
    shutil.copytree(REAL, copy)
    path = copy / "corrections" / "corrections.json"
    corrections = json.loads(path.read_text(encoding="utf-8"))
    for correction in corrections.values():
        correction["reviewed_by"] = "human"
        assert correction["reason"] and correction["example"]
    path.write_text(json.dumps(corrections), encoding="utf-8")
    assert load_rules(copy).report.corrections_not_applied == []
