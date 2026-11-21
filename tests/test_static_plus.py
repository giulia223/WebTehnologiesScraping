"""Static+: script-URL rules on URLs inside inline scripts (Task 7.3)."""

from pathlib import Path

import pytest
from raw_sites import Site

from techdetect.evaluate.static_plus import browser_verdict
from techdetect.fingerprints.loader import RuleSet, load_rules
from techdetect.fingerprints.matcher import (
    INLINE_URL_KEY,
    INLINE_URLS,
    SOURCES,
    RuleIndex,
    inline_script_urls,
    match_domain,
)

REAL_RULES = Path(__file__).parents[1] / "fingerprints"
WITH_INLINE = SOURCES | {INLINE_URLS}

HOTJAR_SNIPPET = """<html><head><script>
(function(h,o,t,j,a,r){h.hj=h.hj||function(){};r=o.createElement('script');
r.src=t+h._hjSettings.hjid+j;a.appendChild(r);
})(window,document,'https://static.hotjar.com/c/hotjar-','.js?sv=');
</script></head><body></body></html>"""


@pytest.fixture(scope="module")
def index() -> RuleIndex:
    return RuleIndex(load_rules(REAL_RULES))


@pytest.fixture(scope="module")
def rules(index: RuleIndex) -> RuleSet:
    return index.ruleset


def page(tmp_path: Path, html: str) -> Path:
    path = tmp_path / "page.html"
    path.write_text(html, encoding="utf-8")
    return path


def hotjar_evidence(raw: Path, host: str, index: RuleIndex, sources: frozenset[str]) -> list:
    return [e for e in match_domain(raw, host, index, sources).evidence if e.technology == "Hotjar"]


def test_an_inline_loader_snippet_gives_the_tracker_as_declared(
    tmp_path: Path, index: RuleIndex
) -> None:
    raw = tmp_path / "raw"
    Site(raw, "site.com").static(page(tmp_path, HOTJAR_SNIPPET))
    [evidence] = hotjar_evidence(raw, "site.com", index, WITH_INLINE)
    assert evidence.key == INLINE_URL_KEY
    assert evidence.signal == "scriptSrc"
    assert evidence.source_artifact == "static.html"
    assert evidence.tier == "declared"  # named by the site's code, not seen running
    assert evidence.location == "https://static.hotjar.com/c/hotjar-"
    assert "document,'https://static.hotjar.com" in evidence.context  # the script around it
    # Off by default: normal detection does not read inline URLs.
    assert hotjar_evidence(raw, "site.com", index, SOURCES) == []


def test_a_url_outside_a_script_or_in_a_data_block_gives_nothing(
    tmp_path: Path, index: RuleIndex
) -> None:
    html = """<html><body>
    <div data-config='{"tracker":"https://static.hotjar.com/c/hotjar-1.js"}'></div>
    <script type="application/ld+json">{"sameAs":["https://static.hotjar.com/c/x"]}</script>
    <p>We use https://static.hotjar.com/ for analytics.</p>
    </body></html>"""
    raw = tmp_path / "raw"
    Site(raw, "site.com").static(page(tmp_path, html))
    assert inline_script_urls(html, "https://site.com/") == []
    assert hotjar_evidence(raw, "site.com", index, WITH_INLINE) == []


def test_a_script_tag_is_not_counted_again_as_an_inline_url(
    tmp_path: Path, index: RuleIndex
) -> None:
    html = (
        '<html><head><script src="https://static.hotjar.com/c/hotjar-1.js"></script></head></html>'
    )
    raw = tmp_path / "raw"
    Site(raw, "site.com").static(page(tmp_path, html))
    evidence = hotjar_evidence(raw, "site.com", index, WITH_INLINE)
    assert [e.key for e in evidence] == [None]  # only the normal <script src> match


def test_escaped_slashes_and_protocol_relative_urls() -> None:
    html = r"""<script>var cfg = {"u": "https:\/\/connect.facebook.net\/en_US\/fbevents.js"};
    s.src = "//www.clarity.ms/tag/" + id;</script>"""
    urls = [u for u, *_ in inline_script_urls(html, "https://site.com/")]
    assert urls == [
        "https://connect.facebook.net/en_US/fbevents.js",
        "https://www.clarity.ms/tag/",  # protocol-relative takes the page's scheme
    ]


def test_the_browser_referee(rules: RuleSet) -> None:
    addition = {
        "domain": "site.com",
        "name": "Hotjar",
        "evidence": [{"signal": "scriptSrc", "pattern": "//static\\.hotjar\\.com/"}],
    }
    loaded = ["https://site.com/", "https://static.hotjar.com/c/hotjar-123.js?sv=6"]
    assert browser_verdict(addition, loaded, rules) == "confirmed"
    assert browser_verdict(addition, ["https://site.com/"], rules) == "not_confirmed"
    assert browser_verdict(addition, None, rules) == "no_browser_page"
