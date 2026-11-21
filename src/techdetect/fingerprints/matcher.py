"""Match fingerprint rules against one domain's saved artifacts, offline.

Input: data/raw/<domain>/ (written by `collect`). Output: every raw match with its
evidence: which rule, which artifact, the matched text with some context, and where it
was found. Nothing is combined here: confidence and versions are worked out from these
matches in a later step, and `implies` / `excludes` after that.

Each rule field is matched against the data Wappalyzer's own engine used for it
(`analyze()` in wappalyzer.js and its driver), taken from our artifacts:

  html, text, meta, dom, scriptSrc   static.html and rendered.html (each, separately)
  url                                the final URL of each page
  headers, cookies                   only from the site's own registered domain(s)
  js                                 values read in the browser (browser.json)
  xhr                                host names of the page's xhr/fetch requests
  dns                                dns.json; certIssuer: tls.json; robots: robots.txt

Fields we cannot check offline (script and stylesheet contents, DOM properties, JS chains
the browser was not asked about) are counted as not checked, never treated as absent.
"""

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import regex
from selectolax.lexbor import LexborHTMLParser

from techdetect.domains import registered_domain
from techdetect.fingerprints.loader import Pattern, RuleSet

REGEX_TIMEOUT_S = 0.5  # per search; a slow pattern is counted, never allowed to hang
MAX_VALUE_CHARS = 200
CONTEXT_CHARS = 40
XHR_TYPES = frozenset({"xhr", "fetch"})  # Chrome's extension API reports fetch() as XHR
NOT_CHECKABLE_FIELDS = ("scripts", "css")  # file contents are never saved
# The collection steps whose files can be matched. Normal detection uses all of them; the
# ablation (evaluate/ablation.py) leaves some out to measure what each step adds.
# static = static.html + static.json, browser = rendered.html + browser.json,
# robots = robots.txt (the well-known step).
SOURCES = frozenset({"static", "browser", "dns", "tls", "robots"})
# An option, not a step: also match script-URL rules against URLs written inside the
# static page's inline scripts (a tracker's loader snippet names its script URL). Off by
# default; `detect --inline-urls` and the ablation's "static+" row turn it on (Task 7.3).
INLINE_URLS = "inline_urls"
INLINE_URL_KEY = "inline script URL"
# A URL in script text: http(s):// or protocol-relative //, then a host with a dot.
# Escaped slashes (https:\/\/host) are unescaped first: JSON written into scripts uses them.
INLINE_URL = regex.compile(
    r"(?:https?:)?//[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+"
    r"(?::\d+)?(?:/[^\s\"'`<>()\\]*)?",
    regex.IGNORECASE,
)
# Only code: data blocks (application/ld+json, application/json, text/template...) list
# URLs that are links or settings, not scripts the page loads.
JS_TYPES = frozenset({"", "text/javascript", "application/javascript", "module", "text/ecmascript"})
KEYED_FIELDS = ("headers", "cookies", "meta", "js", "dns")
# Tier (what kind of proof): "declared" = the site states it (a generator tag, a DNS
# verification token, a robots.txt line); everything else shows the thing in use.
DECLARED_FIELDS = frozenset({"meta", "robots"})
DECLARED_DNS_TYPES = frozenset({"txt"})


@dataclass(frozen=True)
class Evidence:
    technology: str
    signal: str  # the rule field: html, headers, scriptSrc, ...
    key: str | None  # header/cookie/meta name, JS chain, DNS type, dom selector
    subkey: str | None  # dom only: exists, text, attributes.<name>
    source_artifact: str  # the file it came from, e.g. "rendered.html"
    pattern: str  # the rule exactly as written
    confidence: int
    version_template: str
    groups: tuple[str | None, ...]  # regex groups, for resolving the version later
    matched_value: str
    context: str
    location: str | None  # the URL (or host) where the value was found
    tier: str  # "observed" or "declared"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DomainMatches:
    domain: str
    site_domains: list[str]  # registered domains counted as "the site" for headers/cookies
    evidence: list[Evidence] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)  # artifact -> "used" or why not
    not_checked: Counter[str] = field(default_factory=Counter)  # field -> patterns
    timeouts: list[tuple[str, str, str]] = field(default_factory=list)  # tech, pattern, where

    def technologies(self) -> set[str]:
        return {e.technology for e in self.evidence}


# ---------------------------------------------------------------- rule index


Entry = tuple[str, Pattern]  # (technology name, pattern)


# A selector that only asks for a <link> whose href contains a text: link[href*='fonts.g'].
LINK_HREF = regex.compile(r"link\[href\*='([^']+)'\]")


class RuleIndex:
    """Patterns grouped by field and key, built once per rule set.

    A header named "server" is then tested only against patterns for "server", not
    against all 15,000 patterns.
    """

    def __init__(self, ruleset: RuleSet) -> None:
        self.ruleset = ruleset
        self.unkeyed: dict[str, list[Entry]] = defaultdict(list)
        self.keyed: dict[str, dict[str, list[Entry]]] = defaultdict(lambda: defaultdict(list))
        self.dom: dict[str, list[Entry]] = defaultdict(list)
        self.cookie_wildcards: list[tuple[regex.Pattern[str], str, Entry]] = []
        # dom rules of the form link[href*='...'] (e.g. Google Fonts), also tested on the
        # stylesheets the page requested: (the text the href must contain, entry).
        self.link_href: list[tuple[str, Entry]] = []
        self.counts: Counter[str] = Counter()
        for tech in ruleset.technologies.values():
            for pattern in tech.patterns:
                entry = (tech.name, pattern)
                self.counts[pattern.field] += 1
                if pattern.field == "dom":
                    self.dom[pattern.key or ""].append(entry)
                    link = LINK_HREF.fullmatch(pattern.key or "")
                    if link and pattern.subkey in ("exists", "attributes.href"):
                        self.link_href.append((link.group(1), entry))
                elif pattern.field == "cookies" and pattern.key and "*" in pattern.key:
                    # One rule (Google Analytics "_ga_*": GA4 cookies are "_ga_<id>")
                    # uses * as a wildcard in a cookie name.
                    glob = regex.escape(pattern.key).replace("\\*", ".*")
                    self.cookie_wildcards.append((regex.compile(f"^{glob}$"), pattern.key, entry))
                elif pattern.field in KEYED_FIELDS:
                    self.keyed[pattern.field][pattern.key or ""].append(entry)
                else:
                    self.unkeyed[pattern.field].append(entry)


# ---------------------------------------------------------------- one search


def _tier(pattern: Pattern) -> str:
    if pattern.field in DECLARED_FIELDS:
        return "declared"
    if pattern.field == "dns" and pattern.key in DECLARED_DNS_TYPES:
        return "declared"
    return "observed"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


class _Matcher:
    """Collects the evidence of one domain."""

    def __init__(self, index: RuleIndex, result: DomainMatches) -> None:
        self.index = index
        self.result = result

    def test(
        self,
        entries: list[Entry],
        value: str,
        artifact: str,
        location: str | None,
        key: str | None = None,
        tier: str | None = None,
        surrounding: tuple[str, int, int] | None = None,
    ) -> None:
        """Search `value` with each pattern. `tier` overrides the field's usual tier;
        `surrounding` = (text, start, end) of `value` inside a larger text, so the context
        shown comes from that text (e.g. the script around an inline URL)."""
        for tech, pattern in entries:
            try:
                found = pattern.regex.search(value, timeout=REGEX_TIMEOUT_S)
            except TimeoutError:
                self.result.timeouts.append((tech, pattern.source, artifact))
                continue
            if found is None:
                continue
            start, end = found.span()
            # A presence rule (empty pattern: "this header exists") matches nothing
            # visible; the value itself is then the proof worth showing.
            matched = found.group(0) or value
            if surrounding is None:
                context = value[max(0, start - CONTEXT_CHARS) : end + CONTEXT_CHARS]
            else:
                text, at, until = surrounding
                context = text[max(0, at - CONTEXT_CHARS) : until + CONTEXT_CHARS]
            self.result.evidence.append(
                Evidence(
                    technology=tech,
                    signal=pattern.field,
                    key=key if key is not None else pattern.key,
                    subkey=pattern.subkey,
                    source_artifact=artifact,
                    pattern=pattern.source,
                    confidence=pattern.confidence,
                    version_template=pattern.version,
                    groups=found.groups(),
                    matched_value=_clip(matched, MAX_VALUE_CHARS),
                    context=context,
                    location=location,
                    tier=tier or _tier(pattern),
                )
            )

    def keyed(
        self, field_name: str, key: str, value: str, artifact: str, location: str | None
    ) -> None:
        self.test(self.index.keyed[field_name].get(key, []), value, artifact, location)


# ---------------------------------------------------------------- reading artifacts


def _read_json(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _host(url: str | None) -> str:
    return (urlsplit(url).hostname or "") if url else ""


def _js_text(value: Any) -> str:
    """A JS value as JavaScript would turn it into text (true, not True)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _page_text(html: str) -> str:
    """The page's visible text. Wappalyzer reads `document.body.innerText`, which never
    holds the contents of <script> or <style>: a CSS class named `.ot-dpd-desc` is not the
    word "DPD". Parsed again so the main tree keeps its script tags for scriptSrc."""
    tree = LexborHTMLParser(html)
    for node in tree.css("script, style, noscript, template"):
        node.decompose()
    body = tree.body
    return body.text(separator=" ", strip=True) if body is not None else ""


def site_domains(host: str, static: dict | None, browser: dict | None) -> list[str]:
    """The registered domains that count as the site itself (F3.2, option B): the input's,
    plus a final URL's when the site redirected to another registered domain."""
    domains = [registered_domain(host)]
    for data in (static, browser):
        if data and data.get("status") == "ok":
            final = registered_domain(_host(data.get("final_url")))
            if final and final not in domains:
                domains.append(final)
    return domains


def _same_site(url_or_host: str, domains: list[str]) -> bool:
    host = _host(url_or_host) if "/" in url_or_host else url_or_host
    host = host.lstrip(".")
    return bool(host) and registered_domain(host) in domains


# ---------------------------------------------------------------- per artifact


def _match_page(m: _Matcher, html: str, page_url: str, artifact: str) -> None:
    """html, text, meta, scriptSrc and dom rules on one saved page."""
    index = m.index
    m.test(index.unkeyed["html"], html, artifact, page_url)
    m.test(index.unkeyed["url"], page_url, artifact, page_url)
    tree = LexborHTMLParser(html)
    m.test(index.unkeyed["text"], _page_text(html), artifact, page_url)

    for node in tree.css("meta"):
        attrs = node.attributes
        name = attrs.get("name") or attrs.get("property")
        content = attrs.get("content")
        if name and content is not None:
            m.keyed("meta", name.lower(), content, artifact, page_url)

    for node in tree.css("script[src]"):
        src = node.attributes.get("src")
        if src:
            absolute = urljoin(page_url, src)
            m.test(index.unkeyed["scriptSrc"], absolute, artifact, absolute)

    for selector, entries in index.dom.items():
        try:
            nodes = tree.css(selector)
        except Exception:  # a selector lexbor cannot parse: those rules are not checked
            m.result.not_checked["dom (selector not supported)"] += len(entries)
            continue
        if not nodes:
            continue
        for tech, pattern in entries:
            subkey = pattern.subkey or ""
            if subkey == "exists":
                # Tested on the first element's markup, so the evidence shows what was found.
                shown = _clip(nodes[0].html or "", MAX_VALUE_CHARS)
                m.test([(tech, pattern)], shown, artifact, page_url, key=selector)
            elif subkey == "text":
                for node in nodes:
                    m.test([(tech, pattern)], node.text(strip=True), artifact, page_url)
            elif subkey.startswith("attributes."):
                attr = subkey.removeprefix("attributes.")
                for node in nodes:
                    value = node.attributes.get(attr)
                    if value is not None:
                        m.test([(tech, pattern)], value, artifact, page_url)
            # properties.*: only exist in a live browser; counted once per domain below.


def inline_script_urls(html: str, page_url: str) -> list[tuple[str, str, int, int]]:
    """URLs written inside the page's inline scripts: (absolute URL, script text, start,
    end), each URL once. Scripts with a src, and data blocks such as JSON-LD, are skipped."""
    found: dict[str, tuple[str, str, int, int]] = {}
    for node in LexborHTMLParser(html).css("script"):
        attrs = node.attributes
        if "src" in attrs or (attrs.get("type") or "").strip().lower() not in JS_TYPES:
            continue
        text = (node.text(deep=True) or "").replace("\\/", "/")
        for match in INLINE_URL.finditer(text):
            absolute = urljoin(page_url, match.group(0))
            found.setdefault(absolute, (absolute, text, match.start(), match.end()))
    return list(found.values())


def _match_inline_urls(m: _Matcher, html: str, page_url: str) -> None:
    """scriptSrc rules on each inline-script URL, xhr rules on its host, as in the browser.

    Tier "declared": the site's own code names the script, but nothing was seen running
    (decided by the human, Task 7.3)."""
    for url, text, start, end in inline_script_urls(html, page_url):
        where = (text, start, end)
        m.test(
            m.index.unkeyed["scriptSrc"], url, "static.html", url, INLINE_URL_KEY, "declared", where
        )
        host = _host(url)
        if host:
            m.test(
                m.index.unkeyed["xhr"], host, "static.html", url, INLINE_URL_KEY, "declared", where
            )


def _match_headers(
    m: _Matcher, responses: list[tuple[str, list[list[str]]]], artifact: str, domains: list[str]
) -> None:
    for url, headers in responses:
        if not _same_site(url, domains):
            continue  # a third party's server says nothing about this site (F3.2)
        for name, value in headers:
            m.keyed("headers", name.lower(), value, artifact, url)


def _match_cookies(
    m: _Matcher, cookies: list[dict[str, Any]], artifact: str, domains: list[str]
) -> None:
    for cookie in cookies:
        where = cookie.get("domain") or _host(cookie.get("set_by"))
        if not _same_site(where, domains):
            continue  # e.g. a widget's PHPSESSID on the widget vendor's domain
        name = str(cookie.get("name", "")).lower()
        value = str(cookie.get("value", ""))
        location = cookie.get("set_by") or f"cookie on {where}"
        m.keyed("cookies", name, value, artifact, location)
        for glob, key, entry in m.index.cookie_wildcards:
            if glob.match(name):
                m.test([entry], value, artifact, location, key=key)


def _static_responses(static: dict[str, Any]) -> tuple[list, list]:
    """Header pairs and cookies of the chosen attempt (redirect steps + final page)."""
    attempt = static["attempts"][static["chosen_attempt"] - 1]
    responses = [(step["url"], step["headers"]) for step in attempt.get("steps", [])]
    return responses, attempt.get("cookies", [])


def _browser_responses(browser: dict[str, Any]) -> list[tuple[str, list[list[str]]]]:
    responses = [(browser["final_url"], browser.get("main_document_headers", []))]
    for request in browser.get("requests", []):
        if "response_headers" in request:
            responses.append((request["url"], request["response_headers"]))
    return responses


def _match_page_stylesheets(m: _Matcher, browser: dict[str, Any]) -> None:
    """link[href*=...] rules on the stylesheets the page itself requested.

    A stylesheet pulled in by a CSS @import or a script has no <link> tag in the HTML,
    but it is the site's own choice just the same (Google Fonts loaded this way was a
    blind-set miss). Only requests of the page itself: a frame holds another company's
    page (Google Maps' own font), and script requests are left out on purpose, since
    files loaded by other scripts have generated names that collide with library names
    (measured on the 200 domains: 54 of 60 such additions were wrong).
    """
    for request in browser.get("requests", []):
        if request.get("resource_type") != "stylesheet" or request.get("origin") != "main":
            continue
        url = request.get("url", "")
        for needle, (tech, pattern) in m.index.link_href:
            if needle in url:
                # The URL plays the <link>'s href; for an "exists" rule it is the proof shown.
                # The key says it was a request, not a <link> tag found in the HTML.
                key = f"stylesheet request, as {pattern.key}"
                m.test([(tech, pattern)], url, "browser.json", url, key=key)


def _match_browser_only(m: _Matcher, browser: dict[str, Any]) -> None:
    """js and xhr: data that exists only in the browser."""
    asked = set(browser.get("js_chains_asked", []))
    for chain, entries in m.index.keyed["js"].items():
        if chain not in asked:
            m.result.not_checked["js (chain not asked)"] += len(entries)
    for chain, value in browser.get("js_globals", {}).items():
        m.keyed("js", chain, _js_text(value), "browser.json", browser["final_url"])

    _match_page_stylesheets(m, browser)
    seen_hosts: set[str] = set()
    for request in browser.get("requests", []):
        if request.get("resource_type") in XHR_TYPES:
            host = _host(request.get("url"))
            if host and host not in seen_hosts:  # one test per host, like Wappalyzer
                seen_hosts.add(host)
                m.test(m.index.unkeyed["xhr"], host, "browser.json", request["url"])


def _match_dns(m: _Matcher, dns: dict[str, Any]) -> None:
    for query in dns.get("queries", []):
        if query.get("status") != "ok":
            continue
        rtype = query["type"].lower()
        for record in query.get("records", []):
            m.keyed("dns", rtype, record, "dns.json", query["name"])


def _match_tls(m: _Matcher, tls: dict[str, Any]) -> None:
    # A certificate that does not cover the probed host (e.g. the hosting provider's
    # default) describes the provider's machine, not the site: kept on disk, not used.
    if tls.get("status") != "ok" or tls.get("hostname_match") is not True:
        return
    issuer = dict(tls["certificate"].get("issuer") or [])
    for attribute in ("organizationName", "commonName"):
        if issuer.get(attribute):
            m.test(
                m.index.unkeyed["certIssuer"], issuer[attribute], "tls.json", tls.get("probed_host")
            )


# ---------------------------------------------------------------- one domain


def match_domain(
    raw_dir: Path, host: str, index: RuleIndex, sources: frozenset[str] = SOURCES
) -> DomainMatches:
    """Every raw match for one domain, from its folder under raw_dir.

    `sources` names the collection steps whose files may be matched (default: all). A step
    left out is treated as if it had never run, so the result is what a tool without that
    step would see; its files are listed as "not used: left out"."""
    folder = raw_dir / host
    static = _read_json(folder / "static.json") if "static" in sources else None
    browser = _read_json(folder / "browser.json") if "browser" in sources else None
    result = DomainMatches(domain=host, site_domains=site_domains(host, static, browser))
    m = _Matcher(index, result)

    for field_name in NOT_CHECKABLE_FIELDS:
        result.not_checked[field_name] += index.counts[field_name]
    properties = sum(
        1
        for entries in index.dom.values()
        for _, p in entries
        if (p.subkey or "").startswith("properties.")
    )
    result.not_checked["dom (properties)"] += properties

    # Pages: only a real page is matched. A challenge page belongs to the bot-protection
    # vendor, and an error page may belong to a host or parking service (F2.4).
    if _usable(static, "static.html", folder, result):
        static_html = _read_html(folder / static["html_file"])
        _match_page(m, static_html, static["final_url"], "static.html")
        if INLINE_URLS in sources:
            _match_inline_urls(m, static_html, static["final_url"])
        responses, cookies = _static_responses(static)
        _match_headers(m, responses, "static.json", result.site_domains)
        _match_cookies(m, cookies, "static.json", result.site_domains)
    if _usable(browser, "rendered.html", folder, result):
        _match_page(
            m, _read_html(folder / browser["html_file"]), browser["final_url"], "rendered.html"
        )
        _match_headers(m, _browser_responses(browser), "browser.json", result.site_domains)
        _match_cookies(m, browser.get("cookies", []), "browser.json", result.site_domains)
        _match_browser_only(m, browser)
    else:
        result.not_checked["js (no browser page)"] += index.counts["js"]
        result.not_checked["xhr (no browser page)"] += index.counts["xhr"]

    for source, artifact in (("static", "static.html"), ("browser", "rendered.html")):
        if source not in sources:
            result.artifacts[artifact] = "not used: left out"
    dns = _read_json(folder / "dns.json") if "dns" in sources else None
    if dns is not None:
        _match_dns(m, dns)
        result.artifacts["dns.json"] = "used"
    tls = _read_json(folder / "tls.json") if "tls" in sources else None
    if tls is not None:
        _match_tls(m, tls)
        result.artifacts["tls.json"] = (
            "used"
            if tls.get("hostname_match") is True
            else "not used: certificate does not cover the host (or could not be read)"
        )
    robots_path = folder / "robots.txt"
    if "robots" in sources and robots_path.is_file():
        text = robots_path.read_bytes().decode("utf-8", errors="replace")
        wellknown = _read_json(folder / "wellknown.json") or {}
        url = wellknown.get("robots", {}).get("final_url")
        m.test(index.unkeyed["robots"], text, "robots.txt", url)
        result.artifacts["robots.txt"] = "used"
    return result


def _read_html(path: Path) -> str:
    # Pages are saved as received; decoding errors must not hide the rest of the page.
    return path.read_bytes().decode("utf-8", errors="replace")


def _usable(data: dict | None, artifact: str, folder: Path, result: DomainMatches) -> bool:
    if data is None:
        result.artifacts[artifact] = "not used: step did not run"
        return False
    if data.get("status") != "ok":
        result.artifacts[artifact] = f"not used: {data.get('error_category')}"
        return False
    if not data.get("html_file") or not (folder / data["html_file"]).is_file():
        result.artifacts[artifact] = "not used: page file missing"
        return False
    result.artifacts[artifact] = "used"
    return True
