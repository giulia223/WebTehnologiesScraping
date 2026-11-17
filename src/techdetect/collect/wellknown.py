"""Well-known files: only /robots.txt, written to data/raw/<domain>/wellknown.json.

robots.txt often names the platform without any page being parsed (WordPress's
/wp-admin/, Shopify's /checkout rules, a CMS's sitemap path). It is fetched from the
origin of the static fetch's final URL, so it belongs to the site that served the page.

Many servers answer an unknown path with their normal HTML page and status 200 (a
"soft 404"). Such a body is not a robots.txt and would put a page's markup where rules
are expected, so a body is kept only if it is a 200 that looks like plain text.
"""

import asyncio
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from techdetect.collect.provenance import provenance_header
from techdetect.collect.runner import run_per_domain
from techdetect.collect.static_fetch import (
    ClientFactory,
    classify_error,
    default_client_factory,
    describe_error,
    read_capped,
    static_final_url,
)
from techdetect.collect.storage import domain_dir, sha256_hex, write_atomic, write_json_atomic
from techdetect.config import Config
from techdetect.domains import Domain

# RFC 9309 asks robots.txt parsers to handle at least 500 KiB; the rest may be ignored.
ROBOTS_MAX_BYTES = 500 * 1024
SNIFF_BYTES = 1024
UTF8_BOM = b"\xef\xbb\xbf"


def robots_url(raw_dir: Path, host: str) -> tuple[str, str]:
    """Where to ask, and why: the static fetch's final origin, else https://<input host>."""
    final_url = static_final_url(raw_dir, host)
    if final_url:
        parts = urlsplit(final_url)
        return f"{parts.scheme}://{parts.netloc}/robots.txt", "static_final_url"
    return f"https://{host}/robots.txt", "input_host"


def robots_rejection(status: int, body: bytes) -> str | None:
    """Why this answer is not a robots.txt, or None if the body is kept.

    The body decides, not the Content-Type: some servers send a valid robots.txt as
    text/html, and soft-404 pages come with any type. A real robots.txt starts with a
    rule, a `#` comment or a blank line; markup always starts with `<`.
    """
    if status != 200:
        return "status_not_200"
    head = body[:SNIFF_BYTES]
    if not head.strip():
        return "empty"
    if b"\x00" in head:
        return "binary"
    if head.removeprefix(UTF8_BOM).lstrip().startswith(b"<"):
        return "html_soft_404"
    return None


class RobotsFetcher:
    def __init__(self, config: Config, client_factory: ClientFactory) -> None:
        self.config = config
        self.client_factory = client_factory
        self.headers = {
            "User-Agent": config.user_agent,
            "Accept-Language": config.accept_language,
        }

    async def _get(self, verify: bool, url: str) -> dict[str, Any]:
        # A fresh client per request: no cookies carried over from any other site.
        async with self.client_factory(verify) as client:
            async with asyncio.timeout(self.config.static_timeout_s):
                async with client.stream("GET", url, headers=self.headers) as response:
                    body, truncated = await read_capped(response, ROBOTS_MAX_BYTES)
        return {
            "status": response.status_code,
            "final_url": str(response.url),
            # Pairs, not a dict, as in static.json: repeated headers all survive.
            "headers": [[name, value] for name, value in response.headers.multi_items()],
            "content_type": response.headers.get("content-type"),
            "body": body,
            "truncated": truncated,
        }

    async def fetch(self, host: str) -> tuple[dict[str, Any], bytes | None]:
        """Fetch one domain's robots.txt. Returns wellknown.json and the body to save."""
        provenance = provenance_header(self.config)
        start = time.perf_counter()
        url, source = robots_url(self.config.raw_dir, host)
        robots: dict[str, Any] = {
            "url": url,
            "url_source": source,
            "outcome": None,
            "reason": None,
            "http_status": None,
            "final_url": None,
            "content_type": None,
            "headers": [],
            "tls_verified": True,
            "file": None,
            "sha256": None,
            "bytes": None,
            "truncated": False,
            "error_category": None,
            "error": None,
        }
        answer: dict[str, Any] | None = None
        try:
            try:
                answer = await self._get(True, url)
            except httpx.HTTPError as exc:
                if classify_error(exc) != "tls_error":
                    raise
                # Same rule as the static fetch: look anyway, record the bad certificate.
                robots["tls_verified"] = False
                robots["tls_error"] = describe_error(exc)
                answer = await self._get(False, url)
        except (httpx.HTTPError, TimeoutError) as exc:
            robots.update(
                outcome="failed", error_category=classify_error(exc), error=describe_error(exc)
            )

        kept: bytes | None = None
        if answer is not None:
            body = answer.pop("body")
            robots.update(http_status=answer.pop("status"), **answer)
            robots["bytes"] = len(body)
            robots["sha256"] = sha256_hex(body)  # also for a rejected body: proof of what came
            reason = robots_rejection(robots["http_status"], body)
            if reason is None:
                robots.update(outcome="kept", file="robots.txt")
                kept = body
            else:
                robots.update(outcome="rejected", reason=reason)
        robots["elapsed_ms"] = round((time.perf_counter() - start) * 1000)

        result = {
            "provenance": provenance,
            "domain": host,
            # "ok" = the server answered (kept or rejected); "failed" = no answer at all.
            "status": "failed" if robots["outcome"] == "failed" else "ok",
            "robots": robots,
        }
        return result, kept


def save_wellknown(raw_dir: Path, host: str, result: dict[str, Any], body: bytes | None) -> None:
    """Write robots.txt first, wellknown.json last (a JSON on disk = the step finished)."""
    folder = domain_dir(raw_dir, host)
    robots_path = folder / "robots.txt"
    if body is not None:
        write_atomic(robots_path, body)
    elif robots_path.exists():
        robots_path.unlink()  # a file from an older run must not pose as this run's proof
    write_json_atomic(folder / "wellknown.json", result)


async def collect_wellknown(
    domains: list[Domain], config: Config, client_factory: ClientFactory | None = None
) -> list[dict[str, Any]]:
    """Fetch and save every domain's robots.txt, at most config.concurrency at a time."""
    fetcher = RobotsFetcher(config, client_factory or default_client_factory(config))

    async def one(domain: Domain) -> dict[str, Any]:
        result, body = await fetcher.fetch(domain.host)
        save_wellknown(config.raw_dir, domain.host, result, body)
        return result

    return await run_per_domain(domains, config.concurrency, one)
