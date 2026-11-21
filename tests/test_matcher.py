"""Matcher tests: fixture sites in the collected-artifact layout, matched with the REAL
pinned rules (so the acceptance cases test what will actually run), plus a few tests
with the hand-written mini rules for edge cases.

The HTML pages are files in tests/fixtures/pages/; the JSON around them is built by
raw_sites.Site, so each test shows exactly which headers, cookies and requests it uses.
"""

import json
import shutil
from pathlib import Path

import pytest
from raw_sites import Site, cookie

from techdetect.fingerprints import matcher
from techdetect.fingerprints.loader import load_rules
from techdetect.fingerprints.matcher import (
    CONTEXT_CHARS,
    DomainMatches,
    Evidence,
    RuleIndex,
)

FIXTURES = Path(__file__).parent / "fixtures"
REAL_RULES = Path(__file__).parents[1] / "fingerprints"
MINI_RULES = FIXTURES / "fingerprints" / "mini"


@pytest.fixture(scope="module")
def index() -> RuleIndex:
    return RuleIndex(load_rules(REAL_RULES))


def evidence_for(result: DomainMatches, tech: str, signal: str | None = None) -> list[Evidence]:
    return [
        e
        for e in result.evidence
        if e.technology == tech and (signal is None or e.signal == signal)
    ]


# ------------------------------------------------------------ the six acceptance pages


def test_wordpress(index: RuleIndex, tmp_path: Path) -> None:
    link = '<https://wordpress.example/wp-json/>; rel="https://api.w.org/"'
    result = (
        Site(tmp_path, "wordpress.example").static("wordpress.html", [["Link", link]]).match(index)
    )

    generator = evidence_for(result, "WordPress", "meta")[0]
    assert generator.matched_value == "WordPress 6.6.2"
    assert generator.groups == ("6.6.2",)  # kept for the version step
    assert generator.version_template == "\\1"
    assert generator.tier == "declared"  # the site states it
    assert generator.source_artifact == "static.html"

    script = evidence_for(result, "WordPress", "scriptSrc")[0]
    assert (
        script.location == "https://wordpress.example/wp-includes/js/jquery/jquery.min.js?ver=3.7.1"
    )
    assert script.tier == "observed"
    header = evidence_for(result, "WordPress", "headers")[0]
    assert header.key == "link"
    assert header.location == "https://wordpress.example/"
    assert evidence_for(result, "WordPress", "html")


def test_shopify(index: RuleIndex, tmp_path: Path) -> None:
    site = Site(tmp_path, "shopify.example").static(
        "shopify.html",
        headers=[["x-shopid", "12345"]],
        cookies=[cookie("_shopify_y", "shopify.example")],
    )
    result = site.match(index)

    signals = {e.signal for e in evidence_for(result, "Shopify")}
    assert signals == {"dom", "headers", "cookies"}
    href = evidence_for(result, "Shopify", "dom")[0]
    assert href.subkey == "attributes.href"
    assert href.confidence == 50
    assert "cdn.shopify.com" in href.matched_value


def test_react_spa_is_seen_only_after_javascript(index: RuleIndex, tmp_path: Path) -> None:
    site = Site(tmp_path, "react.example").static("plain.html")
    site.browser("react.html", js={"React.version": "18.3.1"})
    result = site.match(index)

    react = evidence_for(result, "React")
    assert {e.source_artifact for e in react} == {"rendered.html", "browser.json"}
    version = evidence_for(result, "React", "js")[0]
    assert version.key == "React.version"
    assert version.groups == ("18.3.1",)


def test_cloudflare_fronted_site(index: RuleIndex, tmp_path: Path) -> None:
    headers = [["Server", "cloudflare"], ["CF-RAY", "8f1c2a3b4c5d6e7f-OTP"]]
    result = Site(tmp_path, "cloudflare.example").static("plain.html", headers).match(index)

    found = evidence_for(result, "Cloudflare", "headers")
    assert {e.key for e in found} == {"server", "cf-ray"}
    ray = next(e for e in found if e.key == "cf-ray")
    assert ray.matched_value == "8f1c2a3b4c5d6e7f-OTP"  # presence rule: the value is shown
    assert ray.location == "https://cloudflare.example/"


def test_google_analytics(index: RuleIndex, tmp_path: Path) -> None:
    cookies = [cookie("_ga", "analytics.example"), cookie("_ga_ABC123", ".analytics.example")]
    site = Site(tmp_path, "analytics.example").static("analytics.html", cookies=cookies)
    result = site.match(index)

    script = evidence_for(result, "Google Analytics", "scriptSrc")[0]
    assert script.location.startswith("https://www.googletagmanager.com/gtag/js")
    by_key = {e.key: e for e in evidence_for(result, "Google Analytics", "cookies")}
    assert set(by_key) == {"_ga", "_ga_*"}  # "_ga_*" is the GA4 cookie rule
    assert by_key["_ga_*"].version_template == "GA4"


def test_the_word_wordpress_in_text_is_not_wordpress(index: RuleIndex, tmp_path: Path) -> None:
    result = Site(tmp_path, "moved.example").static("mentions_wordpress.html").match(index)
    assert "WordPress" not in result.technologies()


# ------------------------------------------------------------ same-site rule (F3.2)


def test_third_party_cloudflare_and_php_cookie_are_not_the_site(
    index: RuleIndex, tmp_path: Path
) -> None:
    # The same PHPSESSID on the site's own domain would count (checked below).
    cdnjs = "https://cdnjs.cloudflare.com/ajax/libs/jquery/3.6.0/jquery.min.js"
    site = Site(tmp_path, "bistro-site.com").static("plain.html")
    site.browser(
        "cdnjs.html",
        headers=[["Server", "nginx"]],
        requests=[
            {
                "url": cdnjs,
                "resource_type": "script",
                "response_headers": [["cf-ray", "8f1c-OTP"], ["server", "cloudflare"]],
            }
        ],
        cookies=[cookie("PHPSESSID", ".widget-vendor.com")],
    )
    result = site.match(index)

    assert "Cloudflare" not in result.technologies()
    assert "PHP" not in result.technologies()
    # Third-party script URLs are still evidence: that is what scriptSrc rules are for.
    assert evidence_for(result, "jQuery", "scriptSrc")
    assert evidence_for(result, "cdnjs", "scriptSrc")[0].location == cdnjs

    own = Site(tmp_path, "php-site.com").static(
        "plain.html", cookies=[cookie("PHPSESSID", ".php-site.com")]
    )
    assert evidence_for(own.match(index), "PHP", "cookies")


def test_cloudflare_on_the_site_own_subdomain_counts(index: RuleIndex, tmp_path: Path) -> None:
    # Real suffixes (.com) here: registered domains come from the Public Suffix List, and
    # ".example" is not on it. Nothing is fetched.
    asset = "https://static.bistro-assets.com/app.js"
    site = Site(tmp_path, "bistro-assets.com").static("plain.html")
    site.browser(
        "plain.html",
        requests=[
            {"url": asset, "resource_type": "script", "response_headers": [["cf-ray", "8f1c-OTP"]]}
        ],
    )
    result = site.match(index)

    ray = evidence_for(result, "Cloudflare", "headers")[0]
    assert ray.location == asset
    assert ray.source_artifact == "browser.json"
    assert result.site_domains == ["bistro-assets.com"]


# ------------------------------------------------------------ which data is used


def test_challenge_page_is_never_matched(index: RuleIndex, tmp_path: Path) -> None:
    site = Site(tmp_path, "walled.example").static(
        "wordpress.html",
        headers=[["Server", "cloudflare"]],
        status="failed",
        error_category="bot_challenge",
    )
    site.dns(("walled.example", "MX", ["1 aspmx.l.google.com."]))
    result = site.match(index)

    assert result.artifacts["static.html"] == "not used: bot_challenge"
    assert {e.technology for e in result.evidence} == {"Google Workspace"}  # DNS still counts


def test_xhr_rules_match_request_hosts(index: RuleIndex, tmp_path: Path) -> None:
    site = Site(tmp_path, "shop.example").static("plain.html")
    site.browser(
        "plain.html",
        requests=[
            {"url": "https://my.adabra.com/track?event=view", "resource_type": "fetch"},
            {"url": "https://ads.adventive.com/pixel.gif", "resource_type": "image"},
        ],
    )
    result = site.match(index)

    adabra = evidence_for(result, "Adabra", "xhr")[0]
    assert adabra.matched_value == "my.adabra.com"
    assert adabra.location == "https://my.adabra.com/track?event=view"
    assert "Adventive" not in result.technologies()  # an image is not an xhr request


def test_link_rules_see_stylesheets_the_page_requested(index: RuleIndex, tmp_path: Path) -> None:
    # rivithead.com: Google Fonts pulled in by a CSS @import, so there is no <link> tag.
    fonts = "https://fonts.googleapis.com/css2?family=Rajdhani:wght@500&display=swap"
    site = Site(tmp_path, "shop.example").static("plain.html")
    site.browser(
        "plain.html", requests=[{"url": fonts, "resource_type": "stylesheet", "origin": "main"}]
    )
    font = evidence_for(site.match(index), "Google Font API", "dom")[0]
    assert font.matched_value == "fonts.googleapis.com"
    assert font.location == fonts
    assert font.key == "stylesheet request, as link[href*='fonts.g']"  # not a <link> tag


def test_frames_and_script_requests_are_not_the_pages_own(index: RuleIndex, tmp_path: Path) -> None:
    # Google's own font inside a Google Maps frame; a Wix chunk whose name contains
    # "bootstrap" was measured to give false libraries, so scripts are not tested.
    site = Site(tmp_path, "shop.example").static("plain.html")
    site.browser(
        "plain.html",
        requests=[
            {
                "url": "https://fonts.googleapis.com/css?family=Google+Sans+Text:400",
                "resource_type": "stylesheet",
                "origin": "iframe",
            },
            {
                "url": "https://assets.squarespace.com/scripts/async-reserved-cart-bootstrap-x.js",
                "resource_type": "script",
                "origin": "main",
            },
        ],
    )
    found = site.match(index).technologies()
    assert "Google Font API" not in found
    assert "Bootstrap" not in found


def test_dns_tiers_and_certificate_rule(index: RuleIndex, tmp_path: Path) -> None:
    site = Site(tmp_path, "mail.example").static("plain.html")
    site.dns(
        ("mail.example", "MX", ["1 aspmx.l.google.com."]),
        ("mail.example", "TXT", ["cloudflare_dashboard_sso=12345"]),
    )
    site.tls("Let's Encrypt", hostname_match=True)
    result = site.match(index)

    assert evidence_for(result, "Google Workspace", "dns")[0].tier == "observed"  # MX in use
    assert evidence_for(result, "Cloudflare", "dns")[0].tier == "declared"  # a TXT token
    issuer = evidence_for(result, "Let's Encrypt", "certIssuer")[0]
    assert issuer.source_artifact == "tls.json"


def test_certificate_for_other_names_is_not_used(index: RuleIndex, tmp_path: Path) -> None:
    site = Site(tmp_path, "hosted.example").static("plain.html")
    site.tls("Let's Encrypt", hostname_match=False)
    result = site.match(index)

    assert "Let's Encrypt" not in result.technologies()
    assert result.artifacts["tls.json"].startswith("not used")


def test_not_checked_is_counted_never_absent(index: RuleIndex, tmp_path: Path) -> None:
    site = Site(tmp_path, "partial.example").static("plain.html")
    site.browser("plain.html", js={}, asked=["React.version"])
    result = site.match(index)

    assert result.not_checked["scripts"] > 0  # script contents are never saved
    assert result.not_checked["css"] > 0
    assert result.not_checked["js (chain not asked)"] > 0
    assert "js (no browser page)" not in result.not_checked

    no_browser = Site(tmp_path, "static-only.example").static("plain.html").match(index)
    assert no_browser.not_checked["js (no browser page)"] > 0
    assert no_browser.artifacts["rendered.html"] == "not used: step did not run"


def test_evidence_is_short_and_has_context(index: RuleIndex, tmp_path: Path) -> None:
    result = Site(tmp_path, "wordpress.example").static("wordpress.html").match(index)
    html = evidence_for(result, "WordPress", "html")[0]
    assert len(html.matched_value) <= 200
    assert html.matched_value in html.context
    assert len(html.context) <= len(html.matched_value) + 2 * CONTEXT_CHARS


# ------------------------------------------------------------ slow patterns


def test_slow_pattern_times_out_and_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules_dir = tmp_path / "rules"
    shutil.copytree(MINI_RULES, rules_dir)
    slow = {"Slow": {"cats": [1], "html": ["(a|aa)+c"]}, "Fast": {"cats": [1], "html": ["aaa"]}}
    (rules_dir / "custom" / "slow.json").write_text(
        json.dumps({n: {**r, "reviewed_by": "human"} for n, r in slow.items()}), encoding="utf-8"
    )
    monkeypatch.setattr(matcher, "REGEX_TIMEOUT_S", 0.05)

    page = tmp_path / "slow.html"
    page.write_text("<p>" + "a" * 60 + "</p>", encoding="utf-8")
    site = Site(tmp_path / "raw", "slow.example").static(page)
    result = site.match(RuleIndex(load_rules(rules_dir)))

    assert ("Slow", "(a|aa)+c", "static.html") in result.timeouts
    assert "Fast" in result.technologies()  # the other patterns still ran
