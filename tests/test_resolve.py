"""Relations between technologies: implies, excludes, requires (Wappalyzer's behaviour).

Relation rules are tested on a small hand-written rule set (in webappanalyzer's format),
where each case is easy to read; the WooCommerce and jQuery cases use the real rules.
"""

import json
from pathlib import Path

import pytest
from raw_sites import Site

from techdetect.fingerprints.combine import TechnologyResult, combine
from techdetect.fingerprints.loader import RuleSet, load_rules
from techdetect.fingerprints.matcher import RuleIndex
from techdetect.fingerprints.resolve import resolve

REAL_RULES = Path(__file__).parents[1] / "fingerprints"

RELATION_RULES = {
    # A chain where the implies entry lowers the confidence: A -> B (50) -> C.
    "A": {"cats": [1], "implies": ["B\\;confidence:50"]},
    "B": {"cats": [2], "implies": ["C"]},
    "C": {"cats": [2]},
    # A loop: X implies Y, Y implies X.
    "X": {"cats": [2], "implies": ["Y"]},
    "Y": {"cats": [2], "implies": ["X"]},
    # Excludes, and an implied technology that an excluder cannot remove.
    "E": {"cats": [2], "excludes": ["F", "G"], "implies": ["G"]},
    "F": {"cats": [2]},
    "G": {"cats": [2]},
    # A version given by the implies entry.
    "Shop": {"cats": [6], "implies": ["Engine\\;version:2"]},
    "Engine": {"cats": [6]},
    # Requirements: on a technology, on any of two technologies, on a category.
    "Plugin": {"cats": [2], "requires": ["A"]},
    "Addon": {"cats": [2], "requires": ["Nowhere", "B"]},
    "Widget": {"cats": [2], "requiresCategory": [6]},
}


@pytest.fixture(scope="module")
def rules(tmp_path_factory: pytest.TempPathFactory) -> RuleSet:
    root = tmp_path_factory.mktemp("relations")
    upstream = root / "webappanalyzer"
    (upstream / "technologies").mkdir(parents=True)
    (upstream / "technologies" / "r.json").write_text(json.dumps(RELATION_RULES), "utf-8")
    categories = {"1": {"name": "CMS"}, "2": {"name": "Other"}, "6": {"name": "Ecommerce"}}
    (upstream / "categories.json").write_text(json.dumps(categories), "utf-8")
    return load_rules(root)


def found(*named: tuple[str, int]) -> dict[str, TechnologyResult]:
    """Direct detections as combine() returns them (only name and confidence matter)."""
    return {
        name: TechnologyResult(name=name, confidence=c, versions=(), tier="observed", groups=())
        for name, c in named
    }


# ------------------------------------------------------------ implies


def test_implied_confidence_is_the_lower_one_and_passes_down_a_chain(rules: RuleSet) -> None:
    result = resolve(found(("A", 100)), rules).technologies

    assert (result["B"].confidence, result["B"].tier, result["B"].implied_by) == (
        50,
        "inferred",
        "A",
    )
    assert (result["C"].confidence, result["C"].implied_by) == (50, "B")  # min(50, 100)


def test_implying_tech_weaker_than_the_entry_gives_its_own_confidence(rules: RuleSet) -> None:
    result = resolve(found(("A", 30)), rules).technologies
    assert result["B"].confidence == 30  # min(30, 50)


def test_direct_detection_is_not_changed_by_an_implication(rules: RuleSet) -> None:
    result = resolve(found(("A", 100), ("B", 25)), rules).technologies
    assert (result["B"].confidence, result["B"].tier, result["B"].implied_by) == (
        25,
        "observed",
        None,
    )


def test_implies_loop_finishes_and_adds_each_once(rules: RuleSet) -> None:
    result = resolve(found(("X", 100)), rules).technologies
    assert set(result) == {"X", "Y"}
    assert result["X"].tier == "observed"  # Y implies X back, but X was already found


def test_version_from_the_implies_entry(rules: RuleSet) -> None:
    assert resolve(found(("Shop", 100)), rules).technologies["Engine"].versions == ("2",)


# ------------------------------------------------------------ excludes


def test_excludes_remove_direct_detections_but_not_implied_ones(rules: RuleSet) -> None:
    resolution = resolve(found(("E", 100), ("F", 100)), rules)

    assert "F" not in resolution.technologies
    assert resolution.excluded == {"F": "E"}
    # G is excluded by E too, but it is implied by E: excludes run first, implies after.
    assert resolution.technologies["G"].tier == "inferred"


def test_a_weaker_detection_does_not_exclude_a_stronger_one(rules: RuleSet) -> None:
    # The Lodash / Underscore case: a confidence-0 hint cannot remove a sure detection.
    resolution = resolve(found(("E", 0), ("F", 100)), rules)
    assert "F" in resolution.technologies
    assert resolution.excluded == {}


# ------------------------------------------------------------ requires


def test_requirement_met_by_a_direct_detection(rules: RuleSet) -> None:
    assert "Plugin" in resolve(found(("Plugin", 100), ("A", 100)), rules).technologies


def test_requirement_not_met_drops_the_technology_and_says_why(rules: RuleSet) -> None:
    resolution = resolve(found(("Plugin", 100)), rules)
    assert "Plugin" not in resolution.technologies
    assert resolution.unmet_requirements == {"Plugin": ("A",)}


def test_one_of_several_requirements_is_enough_even_if_only_implied(rules: RuleSet) -> None:
    # Addon requires "Nowhere" or "B"; B is only implied (by A). Wappalyzer counts both.
    assert "Addon" in resolve(found(("Addon", 100), ("A", 100)), rules).technologies


def test_requirement_on_a_category(rules: RuleSet) -> None:
    assert "Widget" in resolve(found(("Widget", 100), ("Shop", 100)), rules).technologies
    assert "Widget" not in resolve(found(("Widget", 100), ("A", 100)), rules).technologies


# ------------------------------------------------------------ with the real rules


@pytest.fixture(scope="module")
def real() -> tuple[RuleSet, RuleIndex]:
    ruleset = load_rules(REAL_RULES)
    return ruleset, RuleIndex(ruleset)


def detect(real: tuple[RuleSet, RuleIndex], tmp_path: Path, page: str):
    ruleset, index = real
    site = Site(tmp_path, "shop.example").static(page)
    return resolve(combine(site.match(index).evidence), ruleset)


def test_woocommerce_with_wordpress_gives_php_and_mysql_inferred(
    real: tuple[RuleSet, RuleIndex], tmp_path: Path
) -> None:
    result = detect(real, tmp_path, "woocommerce.html").technologies

    assert result["WooCommerce"].confidence == 100
    assert result["WooCommerce"].versions == ("9.3.3",)
    # In the pinned rules WooCommerce *requires* WordPress (it does not imply it), so
    # WordPress must be found directly; WordPress then implies PHP and MySQL.
    assert result["WordPress"].tier == "observed"
    for implied in ("PHP", "MySQL"):
        assert (result[implied].tier, result[implied].implied_by) == ("inferred", "WordPress")
        assert result[implied].confidence == 100


def test_woocommerce_without_wordpress_is_not_kept(
    real: tuple[RuleSet, RuleIndex], tmp_path: Path
) -> None:
    resolution = detect(real, tmp_path, "woocommerce_only.html")
    assert "WooCommerce" not in resolution.technologies
    assert resolution.unmet_requirements["WooCommerce"] == ("WordPress",)


def test_two_jquery_versions_are_both_listed(
    real: tuple[RuleSet, RuleIndex], tmp_path: Path
) -> None:
    result = detect(real, tmp_path, "two_jquery.html").technologies
    assert result["jQuery"].versions == ("1.12.4", "3.6.0")
