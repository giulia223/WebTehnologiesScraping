"""Script files over plain HTTP, no browser: data/raw/<domain>/js.json + js/<sha256>.js.

A cheaper alternative to the headless browser. The browser finds many
technologies because it runs scripts; here we only *download* them and read their text:

- every `<script src>` in the saved static page (the page asks for these files), and
- the Google Tag Manager container of every `GTM-XXXX` id named in that page. The GTM
  snippet builds the URL `gtm.js?id=GTM-XXXX` at run time, so no `<script src>` names it;
  the container is a plain JavaScript file holding the tags the site configured
  (e.g. the Facebook Pixel or Hotjar URL), which is why it is worth reading.

Nothing is executed. Detection reads these files offline (fingerprints/js_files.py).

Limits, for politeness and disk: at most MAX_FILES files per domain (GTM containers
first, then in page order), MAX_BYTES each (cut and recorded), one file at a time per
domain. As in the static fetch, a bad certificate is recorded and the file fetched anyway.
"""

import asyncio
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from selectolax.lexbor import LexborHTMLParser

from techdetect.collect.provenance import provenance_header
from techdetect.collect.runner import OnResult, run_per_domain
from techdetect.collect.static_fetch import (
    ClientFactory,
    classify_error,
    default_client_factory,
    describe_error,
    read_capped,
)
from techdetect.collect.storage import domain_dir, sha256_hex, write_atomic, write_json_atomic
from techdetect.config import Config
from techdetect.domains import Domain

MAX_FILES = 30
MAX_BYTES = 2 * 1024 * 1024
JS_DIR = "js"
GTM_ID = re.compile(r"\bGTM-[A-Z0-9]{4,10}\b")
GTM_CONTAINER = "https://www.googletagmanager.com/gtm.js?id={}"
SNIFF_BYTES = 512


@dataclass(frozen=True)
class ScriptRef:
    url: str
    kind: str  # "gtm_container" or "script_src"


def script_refs(html: str, page_url: str) -> list[ScriptRef]:
    """The files to download for one page: GTM containers first, then <script src> in
    page order, each URL once, at most MAX_FILES."""
    refs: dict[str, ScriptRef] = {}
    for gtm_id in GTM_ID.findall(html):
        url = GTM_CONTAINER.format(gtm_id)
        refs.setdefault(url, ScriptRef(url, "gtm_container"))
    for node in LexborHTMLParser(html).css("script[src]"):
        url = urljoin(page_url, (node.attributes.get("src") or "").strip())
        if url.startswith(("http://", "https://")):
            refs.setdefault(url, ScriptRef(url, "script_src"))
    return list(refs.values())[:MAX_FILES]


def looks_like_html(body: bytes) -> bool:
    """An error or login page served instead of a script: its markup is not the script."""
    return body[:SNIFF_BYTES].lstrip().startswith(b"<")


def static_page(raw_dir: Path, host: str) -> tuple[str, str] | None:
    """(HTML, final URL) of a successful static fetch, or None."""
    static_path = domain_dir(raw_dir, host) / "static.json"
    static = json.loads(static_path.read_text(encoding="utf-8")) if static_path.is_file() else None
    if not static or static.get("status") != "ok" or not static.get("html_file"):
        return None
    path = domain_dir(raw_dir, host) / static["html_file"]
    if not path.is_file():
        return None
    return path.read_bytes().decode("utf-8", errors="replace"), static["final_url"]


class ScriptFetcher:
    def __init__(self, config: Config, client_factory: ClientFactory) -> None:
        self.config = config
        self.client_factory = client_factory
        self.headers = {
            "User-Agent": config.user_agent,
            "Accept-Language": config.accept_language,
        }

    async def _get(self, verify: bool, url: str) -> tuple[int, str | None, bytes, bool]:
        async with self.client_factory(verify) as client:
            async with asyncio.timeout(self.config.static_timeout_s):
                async with client.stream("GET", url, headers=self.headers) as response:
                    body, truncated = await read_capped(response, MAX_BYTES)
        return response.status_code, response.headers.get("content-type"), body, truncated

    async def fetch_one(self, ref: ScriptRef) -> tuple[dict[str, Any], bytes | None]:
        """One file: its record for js.json, and the body to save (None if not kept)."""
        record: dict[str, Any] = {
            "url": ref.url,
            "kind": ref.kind,
            "outcome": None,
            "http_status": None,
            "content_type": None,
            "tls_verified": True,
            "file": None,
            "sha256": None,
            "bytes": None,
            "truncated": False,
            "error_category": None,
            "error": None,
        }
        try:
            try:
                status, content_type, body, truncated = await self._get(True, ref.url)
            except httpx.HTTPError as exc:
                if classify_error(exc) != "tls_error":
                    raise
                record["tls_verified"] = False
                status, content_type, body, truncated = await self._get(False, ref.url)
        except (httpx.HTTPError, TimeoutError) as exc:
            record.update(
                outcome="failed", error_category=classify_error(exc), error=describe_error(exc)
            )
            return record, None
        sha = sha256_hex(body)
        record.update(
            http_status=status,
            content_type=content_type,
            sha256=sha,
            bytes=len(body),
            truncated=truncated,
        )
        if status != 200 or not body.strip():
            record["outcome"] = "not_saved: status or empty"
            return record, None
        if looks_like_html(body):
            record["outcome"] = "not_saved: html, not a script"
            return record, None
        record.update(outcome="saved", file=f"{JS_DIR}/{sha}.js")
        return record, body

    async def fetch(self, host: str) -> tuple[dict[str, Any], dict[str, bytes]]:
        """Every file of one domain: js.json, and {relative path: body} to save."""
        start = time.perf_counter()
        result: dict[str, Any] = {
            "provenance": provenance_header(self.config),
            "domain": host,
            "status": "ok",
            "error_category": None,
            "files": [],
        }
        page = static_page(self.config.raw_dir, host)
        bodies: dict[str, bytes] = {}
        if page is None:
            # No page, nothing to read script names from: the step failed for this domain.
            result.update(status="failed", error_category="no_static_page")
        else:
            refs = script_refs(*page)
            result["refs_found"] = len(refs)
            for ref in refs:  # one at a time: a site's scripts often live on the site itself
                record, body = await self.fetch_one(ref)
                result["files"].append(record)
                if body is not None:
                    bodies[record["file"]] = body
        result["elapsed_ms"] = round((time.perf_counter() - start) * 1000)
        return result, bodies


def save_js(raw_dir: Path, host: str, result: dict[str, Any], bodies: dict[str, bytes]) -> None:
    """Script files first, js.json last (a JSON on disk = the step finished). Files of an
    older run are removed so they cannot pose as this run's proof."""
    folder = domain_dir(raw_dir, host)
    shutil.rmtree(folder / JS_DIR, ignore_errors=True)
    if bodies:
        (folder / JS_DIR).mkdir(parents=True, exist_ok=True)
    for relative, body in bodies.items():
        write_atomic(folder / relative, body)
    write_json_atomic(folder / "js.json", result)


async def collect_js(
    domains: list[Domain],
    config: Config,
    client_factory: ClientFactory | None = None,
    on_result: OnResult | None = None,
) -> list[dict[str, Any]]:
    """Download and save every domain's script files, at most config.concurrency domains
    at a time."""
    fetcher = ScriptFetcher(config, client_factory or default_client_factory(config))

    async def one(domain: Domain) -> dict[str, Any]:
        result, bodies = await fetcher.fetch(domain.host)
        save_js(config.raw_dir, domain.host, result, bodies)
        return result

    return await run_per_domain(domains, config.concurrency, one, on_result)
