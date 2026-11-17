"""Static fetch: what a plain HTTP client (no JavaScript) sees on each domain.

For every domain this tries https://d, https://www.d and http://d, stops at the first
success, and writes data/raw/<domain>/static.html (the body exactly as received, after
content-encoding such as gzip is undone) and static.json (every attempt, redirect step,
header, cookie, retry and error, with a provenance header).

Success = final status 2xx, a non-empty body, and not a bot-challenge page.
"""

import asyncio
import email.utils
import json
import random
import re
import socket
import ssl
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from selectolax.lexbor import LexborHTMLParser

from techdetect.collect.challenge import detect_challenge
from techdetect.collect.provenance import provenance_header
from techdetect.collect.runner import run_per_domain
from techdetect.collect.storage import domain_dir, sha256_hex, write_atomic, write_json_atomic
from techdetect.config import Config
from techdetect.domains import Domain

MAX_HTML_BYTES = 3 * 1024 * 1024  # bigger pages are cut (and marked truncated)
MAX_RETRIES = 2  # per URL, for timeouts, 5xx and 429
BACKOFF_BASE_S = 1.0
RETRY_AFTER_CAP_S = 60.0  # a server asking for a longer wait is given up on
MAX_META_REFRESHES = 2

# Header sent with every request. Accept says we want HTML, like any browser would.
ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

# When every attempt fails, keep the one that tells the most about the site:
# a challenge page (the site exists, a bot wall answered) > an HTTP answer > no answer.
_INFORMATIVENESS = {
    "bot_challenge": 0,
    "http_4xx": 1,
    "http_5xx": 1,
    "rate_limited": 1,
    "empty_response": 1,
}
_LEAST_INFORMATIVE = 2  # connection-level failures: dns_fail, timeout, tls_error, ...

_DNS_MESSAGES = (
    "getaddrinfo failed",  # Windows
    "name or service not known",  # Linux
    "nodename nor servname",  # macOS
    "temporary failure in name resolution",
    "no address associated with hostname",
)

_META_REFRESH_URL = re.compile(r"url\s*=\s*['\"]?\s*([^'\"]+)", re.IGNORECASE)

ClientFactory = Callable[[bool], httpx.AsyncClient]
Sleep = Callable[[float], Awaitable[None]]


# ---------------------------------------------------------------- classification


def _exception_chain(exc: BaseException) -> Iterable[BaseException]:
    """The exception and everything it was raised from (httpx wraps the OS error)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def describe_error(exc: BaseException) -> str:
    """Error text for the artifact: the httpx error plus its root cause, which often holds
    the useful part (e.g. "[SSL: CERTIFICATE_VERIFY_FAILED] ... self-signed certificate")."""
    text = f"{type(exc).__name__}: {exc}"
    root = list(_exception_chain(exc))[-1]
    if root is not exc:
        text += f" (caused by {type(root).__name__}: {root})"
    return text


def classify_error(exc: BaseException) -> str:
    """Map a failed request to an error category."""
    chain = list(_exception_chain(exc))
    # Timeouts first: TimeoutError is itself an OSError and would match the checks below.
    if any(isinstance(e, httpx.TimeoutException | TimeoutError) for e in chain):
        return "timeout"
    if isinstance(exc, httpx.TooManyRedirects):
        return "redirect_loop"
    if any(isinstance(e, ssl.SSLError) for e in chain):
        return "tls_error"
    if any(isinstance(e, ConnectionRefusedError) for e in chain):
        return "conn_refused"
    messages = " ".join(str(e).lower() for e in chain)
    if any(isinstance(e, socket.gaierror) for e in chain) or any(
        m in messages for m in _DNS_MESSAGES
    ):
        return "dns_fail"
    if "certificate" in messages or "[ssl" in messages:
        return "tls_error"
    return "conn_error"  # reset, unreachable network, protocol error, ...


def classify_status(status: int) -> str:
    """Category for a final HTTP status that is not a success."""
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "http_5xx"
    # 4xx, plus the rare final 3xx (a redirect with no usable Location).
    return "http_4xx"


def retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    """Parse Retry-After: either a number of seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def candidate_urls(host: str) -> list[str]:
    """Addresses tried in order until one succeeds (the browser uses the same order)."""
    return [f"https://{host}/", f"https://www.{host}/", f"http://{host}/"]


def choose_attempt(outcomes: list[str | None]) -> int:
    """Index of the attempt to keep: the success, else the most informative failure.

    On a tie, the earlier address wins. Shared with the browser so both apply one rule.
    """
    return min(
        range(len(outcomes)),
        key=lambda i: (
            outcomes[i] != "ok",
            _INFORMATIVENESS.get(outcomes[i] or "", _LEAST_INFORMATIVE),
            i,
        ),
    )


def meta_refresh_target(body: bytes, base_url: str) -> str | None:
    """URL a `<meta http-equiv="refresh" content="0; url=...">` tag sends to, if any.

    httpx follows HTTP redirects but not this HTML one. A refresh without a URL, or to
    the same URL, only reloads the page and is not followed.
    """
    tree = LexborHTMLParser(body.decode("utf-8", errors="replace"))
    for node in tree.css("meta[http-equiv]"):
        if (node.attributes.get("http-equiv") or "").strip().lower() != "refresh":
            continue
        match = _META_REFRESH_URL.search(node.attributes.get("content") or "")
        if not match:
            return None
        target = urljoin(base_url, match.group(1).strip())
        return target if target != base_url else None
    return None


# ---------------------------------------------------------------- one HTTP exchange


@dataclass
class _Response:
    """One finished GET, after httpx followed its HTTP redirects."""

    status: int
    url: str
    headers: list[tuple[str, str]]
    body: bytes
    truncated: bool
    steps: list[dict[str, Any]] = field(default_factory=list)
    cookies: list[dict[str, Any]] = field(default_factory=list)


def _header_value(headers: list[tuple[str, str]], name: str) -> str | None:
    return next((value for key, value in headers if key.lower() == name), None)


def _step(response: httpx.Response, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "url": str(response.url),
        "status": response.status_code,
        "http_version": response.http_version,
        # Pairs, not a dict: repeated headers such as Set-Cookie must all survive.
        "headers": [[name, value] for name, value in response.headers.multi_items()],
    }


def _cookies(response: httpx.Response) -> list[dict[str, Any]]:
    """Cookies the response set, as the cookie jar accepted them.

    The raw Set-Cookie lines are also kept in the step headers, so a cookie the jar
    rejects (e.g. for a foreign domain) is still on record.
    """
    return [
        {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.secure,
            "expires": cookie.expires,
            "set_by": str(response.url),
        }
        for cookie in response.cookies.jar
    ]


async def read_capped(response: httpx.Response, cap: int) -> tuple[bytes, bool]:
    """Read at most `cap` bytes. Stopping early also avoids downloading a huge page."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        if size + len(chunk) > cap:
            chunks.append(chunk[: cap - size])
            return b"".join(chunks), True
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks), False


# ---------------------------------------------------------------- fetcher


def default_client_factory(config: Config) -> ClientFactory:
    """Real httpx clients; one SSL context is built once and shared by all of them."""
    verified_context = httpx.create_ssl_context()

    def make(verify: bool) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            verify=verified_context if verify else False,
            timeout=httpx.Timeout(config.static_timeout_s),
        )

    return make


class StaticFetcher:
    def __init__(
        self, config: Config, client_factory: ClientFactory, sleep: Sleep = asyncio.sleep
    ) -> None:
        self.config = config
        self.client_factory = client_factory
        self.sleep = sleep  # replaceable so tests can check waits without waiting
        self.headers = {
            "User-Agent": config.user_agent,
            "Accept-Language": config.accept_language,
            "Accept": ACCEPT,
        }

    async def _get(self, client: httpx.AsyncClient, url: str) -> _Response:
        # httpx's timeout applies per operation (connect, each read...). The overall
        # deadline stops a server that keeps sending a byte now and then.
        async with asyncio.timeout(self.config.static_timeout_s):
            async with client.stream("GET", url, headers=self.headers) as response:
                body, truncated = await read_capped(response, MAX_HTML_BYTES)
        hops = [*response.history, response]
        return _Response(
            status=response.status_code,
            url=str(response.url),
            headers=list(response.headers.multi_items()),
            body=body,
            truncated=truncated,
            steps=[_step(r, "redirect") for r in response.history] + [_step(response, "final")],
            cookies=[c for r in hops for c in _cookies(r)],
        )

    def _backoff(self, retry_number: int) -> float:
        # Exponential, plus jitter so many clients retrying together don't stay in step.
        return BACKOFF_BASE_S * 2**retry_number + random.uniform(0, BACKOFF_BASE_S)

    async def _wait(
        self, record: dict[str, Any], reason: str, status: int | None, s: float
    ) -> None:
        record["retries"].append({"reason": reason, "status": status, "wait_s": round(s, 2)})
        await self.sleep(s)

    async def _get_with_retries(
        self, client: httpx.AsyncClient, url: str, record: dict[str, Any]
    ) -> _Response:
        """GET with up to MAX_RETRIES retries for timeouts, 5xx and 429."""
        for retry in range(MAX_RETRIES + 1):
            last_try = retry == MAX_RETRIES
            try:
                response = await self._get(client, url)
            except (httpx.TimeoutException, TimeoutError):
                if last_try:
                    raise
                await self._wait(record, "timeout", None, self._backoff(retry))
                continue

            if response.status == 429:
                asked = retry_after_seconds(_header_value(response.headers, "retry-after"))
                if asked is not None and asked > RETRY_AFTER_CAP_S:
                    record["error"] = (
                        f"server asked to wait {asked:.0f} s (cap {RETRY_AFTER_CAP_S:.0f} s)"
                    )
                    return response
                if last_try:
                    return response
                wait = asked if asked is not None else self._backoff(retry)
                await self._wait(record, "rate_limited", 429, wait)
                continue

            if response.status >= 500 and not last_try:
                await self._wait(record, "http_5xx", response.status, self._backoff(retry))
                continue
            return response
        raise AssertionError("unreachable: the last try always returns or raises")

    async def _follow(
        self, client: httpx.AsyncClient, url: str, record: dict[str, Any]
    ) -> _Response:
        """GET `url`, then follow up to MAX_META_REFRESHES HTML meta refreshes."""
        response = await self._get_with_retries(client, url, record)
        for _ in range(MAX_META_REFRESHES):
            if not 200 <= response.status < 300:
                break
            if detect_challenge(response.status, response.headers, response.body):
                break
            target = meta_refresh_target(response.body, response.url)
            if target is None:
                break
            response.steps[-1]["kind"] = "meta_refresh"
            response.steps[-1]["refresh_to"] = target
            record["steps"].extend(response.steps)
            record["cookies"].extend(response.cookies)
            response = await self._get_with_retries(client, target, record)
        record["steps"].extend(response.steps)
        record["cookies"].extend(response.cookies)
        return response

    async def _attempt(
        self, clients: dict[bool, httpx.AsyncClient], url: str
    ) -> tuple[dict[str, Any], _Response | None]:
        """One candidate URL. Returns its record and the response, if any arrived."""
        record: dict[str, Any] = {
            "url": url,
            "outcome": None,
            "error": None,
            "tls_verified": True,
            "retries": [],
            "elapsed_ms": 0,
            "steps": [],
            "cookies": [],
        }
        start = time.perf_counter()
        response: _Response | None = None
        try:
            try:
                response = await self._follow(clients[True], url, record)
            except httpx.HTTPError as exc:
                if classify_error(exc) != "tls_error":
                    raise
                # Bad certificate: look anyway, but record that the site's identity was
                # not verified and why.
                record["tls_verified"] = False
                record["tls_error"] = describe_error(exc)
                response = await self._follow(clients[False], url, record)
        except (httpx.HTTPError, TimeoutError) as exc:
            record["outcome"] = classify_error(exc)
            record["error"] = describe_error(exc)
        finally:
            record["elapsed_ms"] = round((time.perf_counter() - start) * 1000)

        if response is not None:
            vendor = detect_challenge(response.status, response.headers, response.body)
            if vendor:
                record["outcome"] = "bot_challenge"
                record["challenge"] = vendor
            elif 200 <= response.status < 300:
                record["outcome"] = "ok" if response.body.strip() else "empty_response"
            else:
                record["outcome"] = classify_status(response.status)
        return record, response

    async def fetch(self, host: str) -> tuple[dict[str, Any], bytes | None]:
        """Fetch one domain. Returns the static.json content and the body to save."""
        provenance = provenance_header(self.config)  # collected_at = when the fetch started
        start = time.perf_counter()
        attempts: list[dict[str, Any]] = []
        responses: list[_Response | None] = []

        # A fresh pair of clients per domain: each client has a cookie jar, and cookies
        # from one site must never be sent to another.
        async with self.client_factory(True) as verified, self.client_factory(False) as insecure:
            clients = {True: verified, False: insecure}
            for url in candidate_urls(host):
                record, response = await self._attempt(clients, url)
                attempts.append(record)
                responses.append(response)
                if record["outcome"] == "ok":
                    break

        chosen = choose_attempt([a["outcome"] for a in attempts])
        record, response = attempts[chosen], responses[chosen]
        body = response.body if response is not None else None
        result = {
            "provenance": provenance,
            "domain": host,
            "status": "ok" if record["outcome"] == "ok" else "failed",
            "error_category": None if record["outcome"] == "ok" else record["outcome"],
            "chosen_attempt": chosen + 1,
            "final_url": response.url if response else None,
            "http_status": response.status if response else None,
            "challenge": record.get("challenge"),
            "html_file": "static.html" if body is not None else None,
            "html_sha256": sha256_hex(body) if body is not None else None,
            "html_bytes": len(body) if body is not None else None,
            "html_truncated": response.truncated if response else False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000),
            "attempts": attempts,
        }
        return result, body


# ---------------------------------------------------------------- saving and batch


def save_static(raw_dir: Path, host: str, result: dict[str, Any], body: bytes | None) -> None:
    """Write static.html first, static.json last (a JSON on disk = the step finished)."""
    folder = domain_dir(raw_dir, host)
    html_path = folder / "static.html"
    if body is not None:
        write_atomic(html_path, body)
    elif html_path.exists():
        html_path.unlink()  # a page from an older run must not pose as this run's proof
    write_json_atomic(folder / "static.json", result)


async def collect_static(
    domains: list[Domain],
    config: Config,
    client_factory: ClientFactory | None = None,
    sleep: Sleep = asyncio.sleep,
) -> list[dict[str, Any]]:
    """Fetch and save every domain, at most config.concurrency at a time."""
    fetcher = StaticFetcher(config, client_factory or default_client_factory(config), sleep)

    async def one(domain: Domain) -> dict[str, Any]:
        result, body = await fetcher.fetch(domain.host)
        save_static(config.raw_dir, domain.host, result, body)
        return result

    return await run_per_domain(domains, config.concurrency, one)


def static_final_url(raw_dir: Path, host: str) -> str | None:
    """Final URL of a successful static fetch on disk, or None (failed, or not run yet).

    The TLS and robots.txt probes use it so they look at the site that actually served
    the page (after redirects), not only at the name in the input list.
    """
    path = domain_dir(raw_dir, host) / "static.json"
    if not path.is_file():
        return None
    result = json.loads(path.read_text(encoding="utf-8"))
    return result.get("final_url") if result.get("status") == "ok" else None
