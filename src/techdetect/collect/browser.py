"""Browser collection: what a real Chrome sees after JavaScript has run.

For every domain a headless Chromium tries https://d, https://www.d and http://d (the
same order and success rule as the static fetch, but on its own: it never reads
static.json), lets the page settle, and writes data/raw/<domain>/rendered.html (the DOM
after JavaScript), screenshot.jpg, and browser.json (attempts, final URL, cookies,
scripts, meta tags, iframes, with a provenance header).

A bot challenge is recorded and left alone: no headed retry, no attempt to pass it.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Request,
    Response,
    Route,
    Worker,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from techdetect.collect.challenge import detect_challenge
from techdetect.collect.provenance import provenance_header
from techdetect.collect.runner import run_per_domain
from techdetect.collect.static_fetch import candidate_urls, choose_attempt, classify_status
from techdetect.collect.storage import domain_dir, sha256_hex, write_atomic, write_json_atomic
from techdetect.config import Config
from techdetect.domains import Domain

VIEWPORT = {"width": 1366, "height": 768}
NETWORKIDLE_TIMEOUT_MS = 8_000
SCROLL_STEP_MS = 250
MAX_SCROLL_STEPS = 30  # endless-scroll pages would otherwise never reach a bottom
SETTLE_MS = 2_000
SCREENSHOT_QUALITY = 40
INLINE_SCRIPT_CAP = 50 * 1024
INLINE_TOTAL_CAP = 200 * 1024

# Recorded, then aborted: their URL is the evidence; their bytes cost time and say nothing.
BLOCKED_TYPES = frozenset({"image", "font", "media"})
# Response headers kept for pages and scripts (server, CDN, framework headers).
HEADER_TYPES = frozenset({"document", "script"})

# Chromium's network error codes, as they appear in Playwright's messages
# ("net::ERR_NAME_NOT_RESOLVED at https://..."). First match wins.
_NET_ERRORS = (
    ("ERR_NAME_NOT_RESOLVED", "dns_fail"),
    ("ERR_NAME_RESOLUTION_FAILED", "dns_fail"),
    ("ERR_CONNECTION_REFUSED", "conn_refused"),
    ("ERR_CERT_", "tls_error"),
    ("ERR_SSL_", "tls_error"),
    ("ERR_TOO_MANY_REDIRECTS", "redirect_loop"),
    ("ERR_TIMED_OUT", "timeout"),
    ("ERR_CONNECTION_TIMED_OUT", "timeout"),
    ("ERR_CONNECTION_", "conn_error"),  # reset, closed, aborted, failed
    ("ERR_EMPTY_RESPONSE", "conn_error"),
    ("ERR_ADDRESS_UNREACHABLE", "conn_error"),
    ("ERR_NETWORK_", "conn_error"),
    ("ERR_INTERNET_DISCONNECTED", "conn_error"),
    ("ERR_HTTP2_", "conn_error"),
    ("ERR_INVALID_RESPONSE", "conn_error"),
)

# Reads the page in one round trip. Runs inside the page, after it settled.
_DOM_JS = """() => {
  const scripts = Array.from(document.scripts);
  const metaAttrs = ["name", "property", "http-equiv", "itemprop", "charset", "content"];
  const body = document.body;
  return {
    external: scripts.filter(s => s.src).map(s => s.src),
    inline: scripts.filter(s => !s.src).map(s => ({type: s.type || null, text: s.text || ""})),
    meta: Array.from(document.querySelectorAll("meta")).map(m => Object.fromEntries(
      metaAttrs.filter(a => m.hasAttribute(a)).map(a => [a, m.getAttribute(a)]))),
    hasContent: !!body && (body.innerText.trim().length > 0 || body.children.length > 0),
  };
}"""

# Scroll one screen at a time so lazy-loaded content (images, widgets, tag-manager tags
# triggered by scrolling) has a chance to load, then stop at the bottom.
_SCROLL_JS = """async ([stepMs, maxSteps]) => {
  for (let i = 0; i < maxSteps; i++) {
    window.scrollBy(0, window.innerHeight);
    await new Promise(resolve => setTimeout(resolve, stepMs));
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight) break;
  }
  window.scrollTo(0, 0);
}"""


# ---------------------------------------------------------------- small pure helpers


def browser_user_agent(default_ua: str, tag: str) -> str:
    """Chrome's own User-Agent, "HeadlessChrome" said as "Chrome", with our tag appended.

    Sites see a realistic browser that still says who is crawling (D-004).
    """
    return f"{default_ua.replace('HeadlessChrome', 'Chrome')} {tag}"


def first_line(exc: BaseException) -> str:
    """Playwright messages end with a long call log; the first line holds the reason."""
    return f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"


def classify_browser_error(exc: BaseException) -> str:
    """Map a failed page load to an error category (the static fetch's categories, plus
    `browser_error` for failures that are not the network, e.g. a download, a crash)."""
    if isinstance(exc, PlaywrightTimeoutError):
        return "timeout"
    message = str(exc)
    for code, category in _NET_ERRORS:
        if f"net::{code}" in message:
            return category
    return "browser_error"


def is_certificate_error(exc: BaseException) -> bool:
    # Only certificate problems can be looked past; a server that does not speak TLS
    # (ERR_SSL_PROTOCOL_ERROR) fails the same way with or without checks.
    return "net::ERR_CERT_" in str(exc)


def cap_inline_scripts(scripts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    """Keep inline scripts up to 50 KB each and 200 KB in total.

    Returns (kept, total bytes kept, number dropped). A cut script is marked truncated;
    scripts past the total budget are counted, not silently lost.
    """
    kept: list[dict[str, Any]] = []
    total = 0
    for index, script in enumerate(scripts):
        budget = min(INLINE_SCRIPT_CAP, INLINE_TOTAL_CAP - total)
        if budget <= 0:
            return kept, total, len(scripts) - index
        raw = script["text"].encode("utf-8")
        cut = raw[:budget]
        kept.append(
            {
                "type": script.get("type"),
                "bytes": len(raw),
                "truncated": len(cut) < len(raw),
                # errors="ignore" drops a character split in half by the cut.
                "text": cut.decode("utf-8", errors="ignore"),
            }
        )
        total += len(cut)
    return kept, total, 0


# ---------------------------------------------------------------- one page load


class NetworkRecorder:
    """Records every request of one page load, at browser-context level.

    Context level (not page level) so requests from iframes and workers are caught too:
    many trackers, and Shopify's app and custom pixels, run inside a worker or an iframe.

    Origin: `main` (the page), `iframe` (with the frame's URL), `service_worker`, or
    `other_page` (a popup). Playwright cannot tell a dedicated Web Worker's requests
    apart: they carry the frame that created the worker. Workers seen are listed in
    `workers`, so this limit is visible in the data.

    Images, fonts and media are recorded first, then aborted: the URL is the evidence
    (a tracking pixel, a font service), and not downloading them saves time. Their
    responses (e.g. cookies a pixel would set) are therefore never seen.
    """

    def __init__(self, context: BrowserContext, page: Page) -> None:
        self.context = context
        self.page = page
        self.records: dict[Request, dict[str, Any]] = {}
        self.workers: list[dict[str, str]] = []
        # Header reads are async; they are done once the page has settled.
        self._pending_headers: list[tuple[dict[str, Any], Response]] = []

    # -- wiring

    async def start(self) -> None:
        self.context.on("request", self._on_request)
        self.context.on("response", self._on_response)
        self.context.on("requestfailed", self._on_failed)
        self.page.on("worker", self._on_worker)
        await self.context.route("**/*", self._route)

    async def stop(self) -> None:
        self.context.remove_listener("request", self._on_request)
        self.context.remove_listener("response", self._on_response)
        self.context.remove_listener("requestfailed", self._on_failed)
        await self.context.unroute("**/*", self._route)

    # -- events

    def _record(self, request: Request) -> dict[str, Any]:
        """The record of a request, created on first sight (event or route, either order)."""
        if request not in self.records:
            origin, origin_url = self._origin(request)
            self.records[request] = {
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
                "status": None,
                "failure": None,
                "blocked": False,
                "origin": origin,
                "origin_url": origin_url,
            }
        return self.records[request]

    def _origin(self, request: Request) -> tuple[str, str | None]:
        if request.service_worker is not None:
            return "service_worker", request.service_worker.url
        try:
            frame = request.frame
        except PlaywrightError:  # requests without a frame
            return "unknown", None
        # A document request is the frame's own page: name it by the URL being loaded
        # (at that moment frame.url is still empty or the previous page).
        frame_url = request.url if request.is_navigation_request() else frame.url
        if frame == self.page.main_frame:
            return "main", frame_url
        if frame.parent_frame is None:
            return "other_page", frame_url
        return "iframe", frame_url

    def _on_request(self, request: Request) -> None:
        self._record(request)

    def _on_response(self, response: Response) -> None:
        record = self._record(response.request)
        record["status"] = response.status
        if response.request.resource_type in HEADER_TYPES:
            self._pending_headers.append((record, response))

    def _on_failed(self, request: Request) -> None:
        self._record(request)["failure"] = request.failure

    def _on_worker(self, worker: Worker) -> None:
        self.workers.append({"type": "dedicated", "url": worker.url})

    async def _route(self, route: Route) -> None:
        if route.request.resource_type in BLOCKED_TYPES:
            self._record(route.request)["blocked"] = True  # recorded before it is aborted
            await route.abort()
        else:
            await route.continue_()

    # -- result

    async def finish(self) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        """All request records in order, and the workers seen. Call before the page closes."""
        for record, response in self._pending_headers:
            try:
                headers = await response.headers_array()
            except PlaywrightError:
                continue  # the response is gone (e.g. its frame was removed)
            record["response_headers"] = [[h["name"], h["value"]] for h in headers]
        workers = self.workers + [
            {"type": "service", "url": w.url} for w in self.context.service_workers
        ]
        return list(self.records.values()), workers


@dataclass
class _Page:
    """What one successful page load produced (before deciding if it is a success)."""

    status: int | None
    final_url: str
    headers: list[tuple[str, str]]
    html: bytes
    screenshot: bytes
    dom: dict[str, Any]
    frames: list[str]
    cookies: list[dict[str, Any]]
    accept_language_sent: str | None
    networkidle_reached: bool
    requests: list[dict[str, Any]]
    workers: list[dict[str, str]]
    notes: list[str] = field(default_factory=list)


class BrowserFetcher:
    def __init__(self, config: Config, browser: Browser, user_agent: str) -> None:
        self.config = config
        self.browser = browser
        self.user_agent = user_agent

    async def _new_context(self, ignore_https_errors: bool) -> BrowserContext:
        context = await self.browser.new_context(
            viewport=VIEWPORT,
            # No `locale=` here: combined with a User-Agent override, Playwright then sends
            # "en-US" on page, iframe and worker loads but the header below on the rest.
            # The language is set at launch instead (launch_browser), and this header makes
            # every request carry exactly the configured value, as the static fetch does.
            user_agent=self.user_agent,
            extra_http_headers={"Accept-Language": self.config.accept_language},
            ignore_https_errors=ignore_https_errors,
        )
        context.set_default_timeout(self.config.browser_timeout_s * 1000)
        return context

    async def _settle(self, page: Page, notes: list[str]) -> bool:
        """networkidle (up to 8 s, not required), slow scroll, back to top, 2 s pause."""
        try:
            await page.wait_for_load_state("networkidle", timeout=NETWORKIDLE_TIMEOUT_MS)
            reached = True
        except PlaywrightTimeoutError:
            # Pages with analytics beacons or chat widgets never go quiet; that is normal.
            reached = False
        try:
            await page.evaluate(_SCROLL_JS, [SCROLL_STEP_MS, MAX_SCROLL_STEPS])
        except PlaywrightError as exc:
            # A JavaScript redirect during the scroll destroys the page being scrolled.
            notes.append(f"scroll interrupted: {first_line(exc)}")
            await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(SETTLE_MS)
        return reached

    async def _read(self, page: Page) -> tuple[bytes, dict[str, Any]]:
        """Rendered HTML and DOM facts; tried twice in case a late navigation interrupts."""
        for last_try in (False, True):
            try:
                html = await page.content()
                dom = await page.evaluate(_DOM_JS)
                return html.encode("utf-8"), dom
            except PlaywrightError:
                if last_try:
                    raise
                await page.wait_for_load_state("domcontentloaded")
        raise AssertionError("unreachable")

    async def _load(self, context: BrowserContext, url: str) -> _Page:
        page = await context.new_page()
        documents: list[Response] = []

        def on_response(response: Response) -> None:
            # The main page's documents, in order: the last one is what is on screen,
            # also after a JavaScript redirect that goto() itself does not see.
            if response.request.is_navigation_request() and response.frame == page.main_frame:
                documents.append(response)

        page.on("response", on_response)
        recorder = NetworkRecorder(context, page)
        await recorder.start()
        try:
            await page.goto(
                url, wait_until="domcontentloaded", timeout=self.config.browser_timeout_s * 1000
            )
            notes: list[str] = []
            reached = await self._settle(page, notes)
            html, dom = await self._read(page)
            last = documents[-1] if documents else None
            headers = [(h["name"], h["value"]) for h in await last.headers_array()] if last else []
            sent = await last.request.header_value("accept-language") if last else None
            requests, workers = await recorder.finish()
            return _Page(
                status=last.status if last else None,
                final_url=page.url,
                headers=headers,
                html=html,
                screenshot=await page.screenshot(type="jpeg", quality=SCREENSHOT_QUALITY),
                dom=dom,
                frames=[f.url for f in page.frames if f != page.main_frame],
                cookies=await context.cookies(),
                accept_language_sent=sent,
                networkidle_reached=reached,
                requests=requests,
                workers=workers,
                notes=notes,
            )
        finally:
            await recorder.stop()
            await page.close()

    async def _attempt(
        self, contexts: dict[bool, BrowserContext], url: str
    ) -> tuple[dict[str, Any], _Page | None]:
        """One candidate URL: its record, and the loaded page if one loaded."""
        record: dict[str, Any] = {
            "url": url,
            "outcome": None,
            "error": None,
            "tls_verified": True,
            "networkidle_reached": None,
            "elapsed_ms": 0,
        }
        start = time.perf_counter()
        loaded: _Page | None = None
        try:
            try:
                loaded = await self._load(contexts[False], url)
            except PlaywrightError as exc:
                if not is_certificate_error(exc):
                    raise
                # Same rule as the static fetch: look anyway, record why it was not trusted.
                record["tls_verified"] = False
                record["tls_error"] = first_line(exc)
                if True not in contexts:
                    contexts[True] = await self._new_context(ignore_https_errors=True)
                loaded = await self._load(contexts[True], url)
        except PlaywrightError as exc:
            record["outcome"] = classify_browser_error(exc)
            record["error"] = first_line(exc)
        finally:
            record["elapsed_ms"] = round((time.perf_counter() - start) * 1000)

        if loaded is not None:
            record["networkidle_reached"] = loaded.networkidle_reached
            if loaded.notes:
                record["notes"] = loaded.notes
            vendor = detect_challenge(loaded.status or 0, loaded.headers, loaded.html)
            if vendor:
                record["outcome"] = "bot_challenge"
                record["challenge"] = vendor
            elif loaded.status is None:
                record["outcome"] = "browser_error"  # no document response was seen
                record["error"] = "no main document response"
            elif 200 <= loaded.status < 300:
                record["outcome"] = "ok" if loaded.dom["hasContent"] else "empty_response"
            else:
                record["outcome"] = classify_status(loaded.status)
        return record, loaded

    async def fetch(
        self, host: str, urls: list[str] | None = None
    ) -> tuple[dict[str, Any], _Page | None]:
        """Load one domain. Returns browser.json and the chosen page (for its files).

        `urls` replaces the usual three addresses; tests use it to point at a local server.
        """
        start = time.perf_counter()
        attempts: list[dict[str, Any]] = []
        pages: list[_Page | None] = []
        # One context per domain: its own cookies and storage, never shared between sites.
        # The key says whether certificate errors are ignored; that one is made on demand.
        contexts = {False: await self._new_context(ignore_https_errors=False)}
        try:
            for url in urls or candidate_urls(host):
                record, loaded = await self._attempt(contexts, url)
                attempts.append(record)
                pages.append(loaded)
                if record["outcome"] == "ok":
                    break
        finally:
            for context in contexts.values():
                await context.close()

        chosen = choose_attempt([a["outcome"] for a in attempts])
        record, page = attempts[chosen], pages[chosen]
        ok = record["outcome"] == "ok"
        scripts = page.dom if page else {"external": [], "inline": []}
        inline, inline_bytes, dropped = cap_inline_scripts(scripts["inline"])
        result = {
            "provenance": provenance_header(
                self.config,
                user_agent=self.user_agent,
                # What the browser was seen sending, not only what was configured.
                accept_language=page.accept_language_sent if page else None,
            ),
            "domain": host,
            "status": "ok" if ok else "failed",
            "error_category": None if ok else record["outcome"],
            "challenge": record.get("challenge"),
            "chosen_attempt": chosen + 1,
            "start_url": attempts[0]["url"],
            "final_url": page.final_url if page else None,
            # Filled in by the full collect command once both steps have run.
            "final_url_differs_from_static": None,
            "http_status": page.status if page else None,
            "html_file": "rendered.html" if page else None,
            "html_sha256": sha256_hex(page.html) if page else None,
            "html_bytes": len(page.html) if page else None,
            "screenshot_file": "screenshot.jpg" if page else None,
            "screenshot_sha256": sha256_hex(page.screenshot) if page else None,
            "main_document_headers": [list(h) for h in page.headers] if page else [],
            "cookies": page.cookies if page else [],
            "scripts": {
                "external": scripts["external"],
                "inline": inline,
                "inline_total_bytes": inline_bytes,
                "inline_dropped": dropped,
            },
            "meta": page.dom["meta"] if page else [],
            "iframes": page.frames if page else [],
            "workers": page.workers if page else [],
            "requests": page.requests if page else [],
            "elapsed_ms": round((time.perf_counter() - start) * 1000),
            "attempts": attempts,
        }
        return result, page


# ---------------------------------------------------------------- saving and batch


def save_browser(raw_dir: Path, host: str, result: dict[str, Any], page: _Page | None) -> None:
    """Write rendered.html and screenshot.jpg first, browser.json last."""
    folder = domain_dir(raw_dir, host)
    files = {"rendered.html": page.html if page else None}
    files["screenshot.jpg"] = page.screenshot if page else None
    for name, data in files.items():
        path = folder / name
        if data is not None:
            write_atomic(path, data)
        elif path.exists():
            path.unlink()  # a file from an older run must not pose as this run's proof
    write_json_atomic(folder / "browser.json", result)


async def default_user_agent(browser: Browser) -> str:
    """The User-Agent this Chromium sends by default (it includes the exact version)."""
    context = await browser.new_context()
    try:
        page = await context.new_page()
        return await page.evaluate("navigator.userAgent")
    finally:
        await context.close()


def language_args(accept_language: str) -> list[str]:
    """Chromium flags for the UI language and the language list, from Accept-Language.

    "en-US,en;q=0.9" gives --lang=en-US and --accept-lang=en-US,en, so navigator.language,
    navigator.languages and date/number formatting match the header we send.
    """
    languages = [part.split(";")[0].strip() for part in accept_language.split(",")]
    languages = [lang for lang in languages if lang]
    return [f"--lang={languages[0]}", f"--accept-lang={','.join(languages)}"]


async def launch_browser(playwright: Playwright, config: Config) -> Browser:
    """One headless Chromium for the whole run; each domain gets its own context in it."""
    return await playwright.chromium.launch(
        headless=True, args=language_args(config.accept_language)
    )


async def collect_browser(domains: list[Domain], config: Config) -> list[dict[str, Any]]:
    """Load and save every domain, at most config.browser_concurrency pages at a time."""
    async with async_playwright() as playwright:
        browser = await launch_browser(playwright, config)
        try:
            user_agent = browser_user_agent(await default_user_agent(browser), config.browser_tag)
            fetcher = BrowserFetcher(config, browser, user_agent)

            async def one(domain: Domain) -> dict[str, Any]:
                result, page = await fetcher.fetch(domain.host)
                save_browser(config.raw_dir, domain.host, result, page)
                return result

            return await run_per_domain(domains, config.browser_concurrency, one)
        finally:
            await browser.close()
