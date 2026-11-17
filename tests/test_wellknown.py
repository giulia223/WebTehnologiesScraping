"""robots.txt tests. Every response comes from httpx.MockTransport: no network."""

import asyncio
import json
import ssl
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from techdetect.collect.storage import sha256_hex
from techdetect.collect.wellknown import collect_wellknown, robots_rejection, robots_url
from techdetect.config import Config
from techdetect.domains import Domain

Handler = Callable[[httpx.Request], httpx.Response]
ROBOTS = b"User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\n"
SOFT_404 = b"\n  <!DOCTYPE html><html><head><title>Not found</title></head><body></body></html>"


def factory(handler: Handler, insecure_handler: Handler | None = None):
    def make(verify: bool) -> httpx.AsyncClient:
        chosen = handler if verify or insecure_handler is None else insecure_handler
        return httpx.AsyncClient(transport=httpx.MockTransport(chosen), follow_redirects=True)

    return make


def run(tmp_path: Path, handler: Handler, insecure_handler: Handler | None = None) -> dict:
    config = Config(raw_dir=tmp_path)
    domain = Domain(1, "d.com", "d.com")
    asyncio.run(collect_wellknown([domain], config, factory(handler, insecure_handler)))
    return json.loads((tmp_path / "d.com" / "wellknown.json").read_text(encoding="utf-8"))


def write_static(raw_dir: Path, host: str, status: str, final_url: str | None) -> None:
    folder = raw_dir / host
    folder.mkdir(parents=True, exist_ok=True)
    static = {"domain": host, "status": status, "final_url": final_url}
    (folder / "static.json").write_text(json.dumps(static), encoding="utf-8")


# ------------------------------------------------------------ the keep rule


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (200, ROBOTS, None),
        (200, b"\xef\xbb\xbf# comment first\nUser-agent: *\n", None),
        (200, SOFT_404, "html_soft_404"),
        (200, b"\xef\xbb\xbf<html><body>home</body></html>", "html_soft_404"),
        (200, b"  \n\t", "empty"),
        (200, b"\x89PNG\r\n\x1a\n\x00\x00", "binary"),
        (404, ROBOTS, "status_not_200"),
    ],
)
def test_robots_rejection(status: int, body: bytes, reason: str | None) -> None:
    assert robots_rejection(status, body) == reason


# ------------------------------------------------------------ fetching and saving


def test_plain_robots_txt_is_kept_and_saved(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=ROBOTS, headers={"content-type": "text/plain"})

    saved = run(tmp_path, handler)
    robots = saved["robots"]

    assert str(seen[0].url) == "https://d.com/robots.txt"
    assert seen[0].headers["accept-language"] == Config().accept_language
    assert saved["status"] == "ok"
    assert robots["outcome"] == "kept"
    assert robots["file"] == "robots.txt"
    assert (tmp_path / "d.com" / "robots.txt").read_bytes() == ROBOTS
    assert robots["sha256"] == sha256_hex(ROBOTS)
    assert robots["content_type"] == "text/plain"
    assert saved["provenance"]["accept_language"] == Config().accept_language
    assert not list(tmp_path.rglob("*.tmp"))


def test_html_page_served_as_robots_txt_is_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "d.com"
    folder.mkdir()
    (folder / "robots.txt").write_bytes(ROBOTS)  # left over from an older run

    def handler(request: httpx.Request) -> httpx.Response:
        # A soft 404: the site's normal HTML page, with status 200.
        return httpx.Response(200, content=SOFT_404, headers={"content-type": "text/html"})

    robots = run(tmp_path, handler)["robots"]

    assert robots["outcome"] == "rejected"
    assert robots["reason"] == "html_soft_404"
    assert robots["file"] is None
    assert not (folder / "robots.txt").exists()


def test_not_found_is_rejected(tmp_path: Path) -> None:
    robots = run(tmp_path, lambda r: httpx.Response(404, content=b"Not Found"))["robots"]
    assert robots["outcome"] == "rejected"
    assert robots["reason"] == "status_not_200"
    assert robots["http_status"] == 404


def test_plain_body_sent_as_text_html_is_kept(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ROBOTS, headers={"content-type": "text/html"})

    robots = run(tmp_path, handler)["robots"]
    assert robots["outcome"] == "kept"
    assert robots["content_type"] == "text/html"


def test_url_comes_from_the_static_final_origin(tmp_path: Path) -> None:
    write_static(tmp_path, "d.com", "ok", "http://www.d.com/en/home?x=1")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=ROBOTS)

    robots = run(tmp_path, handler)["robots"]
    assert seen == ["http://www.d.com/robots.txt"]
    assert robots["url_source"] == "static_final_url"


def test_failed_static_falls_back_to_input_host(tmp_path: Path) -> None:
    write_static(tmp_path, "d.com", "failed", "https://www.d.com/")
    assert robots_url(tmp_path, "d.com") == ("https://d.com/robots.txt", "input_host")


def test_bad_certificate_is_fetched_anyway_and_recorded(tmp_path: Path) -> None:
    def verified(request: httpx.Request) -> httpx.Response:
        error = httpx.ConnectError("certificate verify failed")
        error.__cause__ = ssl.SSLCertVerificationError("self-signed certificate")
        raise error

    robots = run(tmp_path, verified, lambda r: httpx.Response(200, content=ROBOTS))["robots"]
    assert robots["outcome"] == "kept"
    assert robots["tls_verified"] is False
    assert "self-signed" in robots["tls_error"]


def test_connection_failure_is_recorded(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        error = httpx.ConnectError("refused")
        error.__cause__ = ConnectionRefusedError(10061, "refused")
        raise error

    saved = run(tmp_path, handler)
    assert saved["status"] == "failed"
    assert saved["robots"]["outcome"] == "failed"
    assert saved["robots"]["error_category"] == "conn_refused"
