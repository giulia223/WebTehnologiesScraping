"""Browser collection tests.

The first group needs no browser and always runs. The second group drives a real
headless Chromium against pages served from 127.0.0.1 (the network guard allows
loopback); it is skipped where Chromium is not installed, e.g. in CI.
"""

import asyncio
import functools
import http.server
import json
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from techdetect.collect.browser import (
    INLINE_SCRIPT_CAP,
    INLINE_TOTAL_CAP,
    BrowserFetcher,
    browser_user_agent,
    cap_inline_scripts,
    classify_browser_error,
    default_user_agent,
    first_line,
    is_certificate_error,
    language_args,
    launch_browser,
    save_browser,
)
from techdetect.config import Config
from techdetect.fingerprints.loader import RuleSet, load_rules

FIXTURES = Path(__file__).parent / "fixtures"

# ------------------------------------------------------------ no browser needed


def goto_error(code: str) -> PlaywrightError:
    return PlaywrightError(f"Page.goto: net::{code} at https://d.com/\nCall log:\n  - navigating")


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (goto_error("ERR_NAME_NOT_RESOLVED"), "dns_fail"),
        (goto_error("ERR_CONNECTION_REFUSED"), "conn_refused"),
        (goto_error("ERR_CERT_AUTHORITY_INVALID"), "tls_error"),
        (goto_error("ERR_SSL_PROTOCOL_ERROR"), "tls_error"),
        (goto_error("ERR_TOO_MANY_REDIRECTS"), "redirect_loop"),
        (goto_error("ERR_CONNECTION_TIMED_OUT"), "timeout"),
        (goto_error("ERR_CONNECTION_RESET"), "conn_error"),
        (goto_error("ERR_EMPTY_RESPONSE"), "conn_error"),
        (PlaywrightTimeoutError("Timeout 30000ms exceeded."), "timeout"),
        (PlaywrightError("Page.goto: Download is starting"), "browser_error"),
    ],
)
def test_classify_browser_error(exc: Exception, category: str) -> None:
    assert classify_browser_error(exc) == category


def test_only_certificate_errors_are_looked_past() -> None:
    assert is_certificate_error(goto_error("ERR_CERT_DATE_INVALID"))
    assert not is_certificate_error(goto_error("ERR_SSL_PROTOCOL_ERROR"))


def test_first_line_drops_the_call_log() -> None:
    message = first_line(goto_error("ERR_NAME_NOT_RESOLVED"))
    assert message == "Error: Page.goto: net::ERR_NAME_NOT_RESOLVED at https://d.com/"


def test_user_agent_says_chrome_and_carries_our_tag() -> None:
    default = "Mozilla/5.0 (X11) AppleWebKit/537.36 HeadlessChrome/159.0 Safari/537.36"
    ua = browser_user_agent(default, "techdetect/0.1.0")
    assert ua == "Mozilla/5.0 (X11) AppleWebKit/537.36 Chrome/159.0 Safari/537.36 techdetect/0.1.0"


def test_language_flags_follow_accept_language() -> None:
    assert language_args("en-US,en;q=0.9") == ["--lang=en-US", "--accept-lang=en-US,en"]
    assert language_args("da-DK, da;q=0.8, en;q=0.5") == [
        "--lang=da-DK",
        "--accept-lang=da-DK,da,en",
    ]


def test_inline_scripts_are_capped_each_and_in_total() -> None:
    big = {"type": None, "text": "x" * (INLINE_SCRIPT_CAP + 10)}
    kept, total, dropped = cap_inline_scripts([big] * 5)

    assert len(kept) == INLINE_TOTAL_CAP // INLINE_SCRIPT_CAP  # 4 fit in the budget
    assert all(s["truncated"] and s["bytes"] == INLINE_SCRIPT_CAP + 10 for s in kept)
    assert total == INLINE_TOTAL_CAP
    assert dropped == 1  # counted, not silently lost


def test_inline_script_cut_inside_a_character_stays_valid_text() -> None:
    text = "a" * (INLINE_SCRIPT_CAP - 1) + "é"  # é is 2 bytes in UTF-8: the cut splits it
    kept, _, _ = cap_inline_scripts([{"type": None, "text": text}])
    assert kept[0]["text"] == "a" * (INLINE_SCRIPT_CAP - 1)
    assert kept[0]["truncated"] is True


# ------------------------------------------------------------ real browser, local pages


@functools.cache
def chromium_available() -> bool:
    async def launch() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            await browser.close()

    try:
        asyncio.run(launch())
    except Exception:
        return False
    return True


@pytest.fixture
def browser_ready() -> None:
    if not chromium_available():
        pytest.skip("Chromium is not installed (run: playwright install chromium)")


class LocalSite:
    """Serves tests/fixtures on 127.0.0.1 and remembers the headers of every request."""

    def __init__(self) -> None:
        self.headers: dict[str, dict[str, str]] = {}
        site = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, directory=str(FIXTURES), **kwargs)

            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                site.headers[self.path] = dict(self.headers)
                super().do_GET()

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"{self.base}{path}"


@pytest.fixture(scope="module")
def site() -> Iterator[LocalSite]:
    local = LocalSite()
    yield local
    local.server.shutdown()


def load(
    urls: list[str], config: Config | None = None, rules: RuleSet | None = None
) -> tuple[dict[str, Any], Any]:
    """Run the real fetcher on the given URLs with a fresh browser."""
    config = config or Config()

    async def go() -> tuple[dict[str, Any], Any]:
        async with async_playwright() as playwright:
            browser = await launch_browser(playwright, config)
            try:
                ua = browser_user_agent(await default_user_agent(browser), config.browser_tag)
                fetcher = BrowserFetcher(
                    config,
                    browser,
                    ua,
                    js_chains=rules.js_chains() if rules else None,
                    ruleset_version=rules.version if rules else None,
                )
                return await fetcher.fetch("local.test", urls=urls)
            finally:
                await browser.close()

    return asyncio.run(go())


def test_page_is_rendered_and_saved(browser_ready: None, site: LocalSite, tmp_path: Path) -> None:
    result, page = load([site.url("/browser/page.html")])
    save_browser(tmp_path, "local.test", result, page)

    assert result["status"] == "ok"
    assert result["http_status"] == 200
    assert result["final_url"] == site.url("/browser/page.html")
    # JavaScript ran: the element and the cookie exist only after app.js executed.
    html = (tmp_path / "local.test" / "rendered.html").read_text(encoding="utf-8")
    assert 'id="added-by-js"' in html
    assert any(c["name"] == "fixture_seen" for c in result["cookies"])
    assert result["scripts"]["external"] == [site.url("/browser/app.js")]
    inline = result["scripts"]["inline"]
    assert inline[0]["text"] == 'window.inlineMarker = "inline-1";'
    assert inline[1]["type"] == "application/ld+json"
    assert {"name": "generator", "content": "FixturePress 1.0"} in result["meta"]
    assert {"property": "og:site_name", "content": "Fixture"} in result["meta"]
    assert result["iframes"] == [site.url("/browser/frame.html")]
    screenshot = (tmp_path / "local.test" / "screenshot.jpg").read_bytes()
    assert screenshot.startswith(b"\xff\xd8")  # JPEG
    saved = json.loads((tmp_path / "local.test" / "browser.json").read_text(encoding="utf-8"))
    assert saved["html_sha256"] == result["html_sha256"]
    assert not list(tmp_path.rglob("*.tmp"))


def test_browser_sends_configured_language_and_tagged_user_agent(
    browser_ready: None, site: LocalSite
) -> None:
    config = Config()
    site.headers.clear()
    result, _ = load([site.url("/browser/page.html")], config)

    # The same value on the page, its script and its iframe, as a real Chrome sends.
    for path in ("/browser/page.html", "/browser/app.js", "/browser/frame.html"):
        assert site.headers[path]["Accept-Language"] == config.accept_language, path
    sent = site.headers["/browser/page.html"]
    assert result["provenance"]["accept_language"] == config.accept_language
    assert "HeadlessChrome" not in sent["User-Agent"]
    assert sent["User-Agent"].endswith(config.browser_tag)
    assert result["provenance"]["user_agent"] == sent["User-Agent"]


def test_javascript_redirect_ends_on_the_new_page(browser_ready: None, site: LocalSite) -> None:
    result, _ = load([site.url("/browser/js_redirect.html")])

    assert result["status"] == "ok"
    assert result["start_url"] == site.url("/browser/js_redirect.html")
    assert result["final_url"] == site.url("/browser/page.html")


def test_challenge_page_is_recorded_not_a_crash(browser_ready: None, site: LocalSite) -> None:
    result, page = load([site.url("/challenges/cloudflare.html")])

    assert result["status"] == "failed"
    assert result["error_category"] == "bot_challenge"
    assert result["challenge"] == "cloudflare"
    assert page is not None  # kept as proof of why the site could not be seen


def test_failed_addresses_are_recorded_until_one_works(
    browser_ready: None, site: LocalSite
) -> None:
    urls = [
        site.url(p) for p in ("/browser/missing.html", "/browser/empty.html", "/browser/page.html")
    ]
    result, _ = load(urls)

    assert [a["outcome"] for a in result["attempts"]] == ["http_4xx", "empty_response", "ok"]
    assert result["chosen_attempt"] == 3


# ------------------------------------------------------------ network capture


def request_to(result: dict[str, Any], url_part: str) -> dict[str, Any]:
    return next(r for r in result["requests"] if url_part in r["url"])


def test_tracking_pixel_is_recorded_then_blocked(browser_ready: None, site: LocalSite) -> None:
    result, _ = load([site.url("/browser/tracking.html")])

    pixel = request_to(result, "https://example.test/tr?id=1")
    assert pixel["blocked"] is True
    assert pixel["resource_type"] == "image"
    assert pixel["origin"] == "main"
    assert pixel["status"] is None  # aborted: no response was ever received


def test_requests_carry_their_origin(browser_ready: None, site: LocalSite) -> None:
    result, _ = load([site.url("/browser/tracking.html")])

    page = request_to(result, "/browser/tracking.html")
    assert page["origin"] == "main"
    assert page["status"] == 200
    assert any(name.lower() == "content-type" for name, _ in page["response_headers"])
    assert "response_headers" in request_to(result, "/browser/app.js")  # scripts too

    from_iframe = request_to(result, "data.json?from=iframe")
    assert from_iframe["origin"] == "iframe"
    assert from_iframe["origin_url"] == site.url("/browser/frame_fetch.html")
    # The iframe's own page is named by the URL it loads.
    assert request_to(result, "/browser/frame_fetch.html")["origin_url"] == site.url(
        "/browser/frame_fetch.html"
    )


def test_worker_request_is_captured_but_labelled_by_its_frame(
    browser_ready: None, site: LocalSite
) -> None:
    result, _ = load([site.url("/browser/tracking.html")])

    # Captured: listening at context level sees requests made inside a worker.
    from_worker = request_to(result, "data.json?from=worker")
    assert from_worker["status"] == 200
    # Known limit: Playwright reports a dedicated worker's requests with the frame that
    # created the worker. The worker itself is listed, so the limit is visible.
    assert from_worker["origin"] == "main"
    assert {"type": "dedicated", "url": site.url("/browser/worker.js")} in result["workers"]


# ------------------------------------------------------------ JS globals

MINI_RULES = FIXTURES / "fingerprints" / "mini"
# Chains for the edge cases of tests/fixtures/browser/globals.html.
EDGE_CHAINS = (
    "Nested.inner.count",
    "EmptyValue",
    "LexicalLib.version",
    "Trap.anything",
    "Proto.inherited",
    "Missing.thing",
)


@pytest.fixture
def globals_rules(tmp_path: Path) -> RuleSet:
    """The mini rule set plus a custom rule asking about the fixture page's variables."""
    rules_dir = tmp_path / "rules"
    shutil.copytree(MINI_RULES, rules_dir)
    rule = {
        "FooLib": {"cats": [1], "js": {"FooLib.version": "([\\d.]+)\\;version:\\1", "FooLib": ""}},
        "Edges": {"cats": [1], "js": {chain: "" for chain in EDGE_CHAINS}},
    }
    (rules_dir / "custom" / "globals.json").write_text(json.dumps(rule), encoding="utf-8")
    return load_rules(rules_dir)


def test_js_chains_are_read_like_wappalyzer(
    browser_ready: None, site: LocalSite, globals_rules: RuleSet
) -> None:
    result, _ = load([site.url("/browser/globals.html")], rules=globals_rules)
    values = result["js_globals"]

    assert values["FooLib.version"] == "1.2"  # the acceptance case
    assert values["FooLib"] is True  # an object: present
    assert values["Nested.inner.count"] == 42
    assert values["LexicalLib.version"] == "3.0"  # a `const` global, not on window
    # Wappalyzer's rules: a null root, an inherited property and a missing name are absent.
    for absent in ("EmptyValue", "Proto.inherited", "Missing.thing"):
        assert absent not in values
    assert "Alpha.Version" not in values  # asked (from the mini rules), not on this page


def test_throwing_getter_is_recorded_and_does_not_stop_the_crawl(
    browser_ready: None, site: LocalSite, globals_rules: RuleSet
) -> None:
    result, _ = load([site.url("/browser/globals.html")], rules=globals_rules)

    assert result["status"] == "ok"
    assert "getter exploded" in result["js_chain_errors"]["Trap.anything"]
    assert result["js_globals"]["FooLib.version"] == "1.2"  # the others were still read
    assert result["js_globals_error"] is None


def test_browser_json_records_ruleset_and_the_chains_asked(
    browser_ready: None, site: LocalSite, globals_rules: RuleSet
) -> None:
    result, _ = load([site.url("/browser/globals.html")], rules=globals_rules)

    assert result["ruleset_version"] == globals_rules.version
    assert "+custom:" in result["ruleset_version"]
    assert result["js_chains_asked"] == globals_rules.js_chains()
    assert "Alpha.Version" in result["js_chains_asked"]


def test_window_keys_are_only_the_page_own_variables(
    browser_ready: None, site: LocalSite, globals_rules: RuleSet
) -> None:
    result, _ = load([site.url("/browser/globals.html")], rules=globals_rules)

    assert set(result["window_keys"]) == {"FooLib", "Nested", "EmptyValue", "Trap", "Proto"}
    # Not listed: `const` globals (not properties of window), the browser's own names
    # (window, document...), and our baseline holder (non-enumerable).


def test_frame_indexes_are_not_variables(browser_ready: None, site: LocalSite) -> None:
    result, _ = load([site.url("/browser/page.html")])  # has one iframe: window[0]
    assert "0" not in result["window_keys"]
    assert "inlineMarker" in result["window_keys"]


def test_strict_content_security_policy_falls_back_to_window(
    browser_ready: None, site: LocalSite, globals_rules: RuleSet
) -> None:
    result, _ = load([site.url("/browser/csp.html")], rules=globals_rules)

    assert result["js_globals"]["FooLib.version"] == "1.2"
    assert result["js_chain_errors"] == {}
