"""Static fetch tests. Every response comes from httpx.MockTransport: no network."""

import asyncio
import json
import socket
import ssl
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest

from techdetect.collect import static_fetch
from techdetect.collect.static_fetch import (
    StaticFetcher,
    classify_error,
    classify_status,
    collect_static,
    meta_refresh_target,
    retry_after_seconds,
)
from techdetect.config import Config
from techdetect.domains import Domain

FIXTURES = Path(__file__).parent / "fixtures" / "challenges"
Handler = Callable[[httpx.Request], httpx.Response]
HTML = {"content-type": "text/html; charset=utf-8"}


class FakeSleep:
    """Records requested waits instead of waiting."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def factory(handler: Handler, insecure_handler: Handler | None = None):
    """Client factory: verified clients use `handler`, unverified ones `insecure_handler`."""

    def make(verify: bool) -> httpx.AsyncClient:
        chosen = handler if verify or insecure_handler is None else insecure_handler
        return httpx.AsyncClient(transport=httpx.MockTransport(chosen), follow_redirects=True)

    return make


def run_fetch(handler: Handler, insecure_handler: Handler | None = None, host: str = "d.com"):
    sleep = FakeSleep()
    fetcher = StaticFetcher(Config(), factory(handler, insecure_handler), sleep)
    result, body = asyncio.run(fetcher.fetch(host))
    return result, body, sleep


def connect_error(cause: BaseException, message: str = "connection failed") -> httpx.ConnectError:
    exc = httpx.ConnectError(message)
    exc.__cause__ = cause  # how httpx wraps the underlying OS error
    return exc


# ------------------------------------------------------------ classifiers


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (connect_error(socket.gaierror(11001, "getaddrinfo failed")), "dns_fail"),
        (httpx.ConnectError("[Errno 11001] getaddrinfo failed"), "dns_fail"),
        (httpx.ConnectError("[Errno -2] Name or service not known"), "dns_fail"),
        (httpx.ReadTimeout("timed out"), "timeout"),
        (httpx.ConnectTimeout("timed out"), "timeout"),
        (TimeoutError(), "timeout"),
        (connect_error(ssl.SSLCertVerificationError("certificate verify failed")), "tls_error"),
        (connect_error(ConnectionRefusedError(10061, "refused")), "conn_refused"),
        (httpx.RemoteProtocolError("Server disconnected"), "conn_error"),
        (connect_error(ConnectionResetError(104, "reset")), "conn_error"),
        (httpx.TooManyRedirects("Exceeded maximum allowed redirects."), "redirect_loop"),
    ],
)
def test_classify_error(exc: BaseException, category: str) -> None:
    assert classify_error(exc) == category


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (404, "http_4xx"),
        (403, "http_4xx"),
        (503, "http_5xx"),
        (500, "http_5xx"),
        (429, "rate_limited"),
    ],
)
def test_classify_status(status: int, category: str) -> None:
    assert classify_status(status) == category


def test_retry_after_parsing() -> None:
    now = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
    assert retry_after_seconds("2") == 2.0
    assert retry_after_seconds(format_datetime(now + timedelta(seconds=30)), now) == 30.0
    assert retry_after_seconds("soon") is None
    assert retry_after_seconds(None) is None


def test_meta_refresh_target() -> None:
    page = b'<meta http-equiv="Refresh" content="0; URL=\'/home\'">'
    assert meta_refresh_target(page, "https://d.com/") == "https://d.com/home"
    assert (
        meta_refresh_target(b'<meta http-equiv="refresh" content="300">', "https://d.com/") is None
    )
    same = b'<meta http-equiv="refresh" content="5;url=https://d.com/">'
    assert meta_refresh_target(same, "https://d.com/") is None


# ------------------------------------------------------------ fetch behaviour


def test_two_set_cookie_headers_both_survive() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        headers = [*HTML.items(), ("set-cookie", "a=1; Path=/"), ("set-cookie", "b=2; Path=/")]
        return httpx.Response(200, headers=headers, text="<html>shop</html>")

    result, _, _ = run_fetch(handler)

    step = result["attempts"][0]["steps"][-1]
    assert [v for k, v in step["headers"] if k == "set-cookie"] == ["a=1; Path=/", "b=2; Path=/"]
    assert [c["name"] for c in result["attempts"][0]["cookies"]] == ["a", "b"]


def test_404_on_bare_domain_falls_back_to_www() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "d.com":
            return httpx.Response(404, headers=HTML, text="not found")
        return httpx.Response(200, headers=HTML, text="<html>www site</html>")

    result, body, _ = run_fetch(handler)

    assert result["status"] == "ok"
    assert result["chosen_attempt"] == 2
    assert result["final_url"] == "https://www.d.com/"
    assert [a["outcome"] for a in result["attempts"]] == ["http_4xx", "ok"]
    assert body == b"<html>www site</html>"


def test_http_redirects_are_recorded_as_steps() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(301, headers={"location": "/en/", "x-redirect-by": "Polylang"})
        return httpx.Response(200, headers=HTML, text="<html>en</html>")

    result, _, _ = run_fetch(handler)

    steps = result["attempts"][0]["steps"]
    assert [(s["kind"], s["status"]) for s in steps] == [("redirect", 301), ("final", 200)]
    assert ["x-redirect-by", "Polylang"] in steps[0]["headers"]


def test_meta_refresh_is_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            page = '<html><meta http-equiv="refresh" content="0; url=/home"></html>'
            return httpx.Response(200, headers=HTML, text=page)
        return httpx.Response(200, headers=HTML, text="<html>real home</html>")

    result, body, _ = run_fetch(handler)

    assert result["final_url"] == "https://d.com/home"
    assert body == b"<html>real home</html>"
    steps = result["attempts"][0]["steps"]
    assert [s["kind"] for s in steps] == ["meta_refresh", "final"]
    assert steps[0]["refresh_to"] == "https://d.com/home"


def test_meta_refresh_chain_stops_after_two() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        nxt = {"/": "/a", "/a": "/b", "/b": "/c"}.get(request.url.path)
        page = f'<html><meta http-equiv="refresh" content="0; url={nxt}">hop</html>'
        return httpx.Response(200, headers=HTML, text=page)

    result, _, _ = run_fetch(handler)

    assert requested == ["/", "/a", "/b"]  # /c is never requested
    assert result["final_url"] == "https://d.com/b"


def test_429_with_retry_after_waits_then_retries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "2"})
        return httpx.Response(200, headers=HTML, text="<html>ok</html>")

    result, _, sleep = run_fetch(handler)

    assert sleep.calls == [2.0]
    assert result["status"] == "ok"
    assert result["attempts"][0]["retries"] == [
        {"reason": "rate_limited", "status": 429, "wait_s": 2.0}
    ]


def test_retry_after_above_cap_gives_up_without_waiting() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "120"})

    result, _, sleep = run_fetch(handler)

    assert sleep.calls == []
    assert result["error_category"] == "rate_limited"
    assert "120" in result["attempts"][0]["error"]


def test_5xx_is_retried_twice_per_address() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers=HTML, text="busy")

    result, _, sleep = run_fetch(handler)

    assert result["error_category"] == "http_5xx"
    assert len(sleep.calls) == 2 * 3  # 2 retries for each of the 3 addresses
    assert all(len(a["retries"]) == 2 for a in result["attempts"])


def test_timeout_is_retried_then_categorised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    result, body, sleep = run_fetch(handler)

    assert result["error_category"] == "timeout"
    assert body is None
    assert len(sleep.calls) == 6


def test_tls_error_is_retried_without_verification_and_recorded() -> None:
    def verified(request: httpx.Request) -> httpx.Response:
        raise connect_error(ssl.SSLCertVerificationError("certificate verify failed"))

    def unverified(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=HTML, text="<html>self-signed site</html>")

    result, _, _ = run_fetch(verified, unverified)

    attempt = result["attempts"][0]
    assert result["status"] == "ok"
    assert attempt["tls_verified"] is False
    assert "certificate verify failed" in attempt["tls_error"]


def test_challenge_is_kept_and_preferred_over_other_failures() -> None:
    challenge = (FIXTURES / "cloudflare.html").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "d.com" and request.url.scheme == "https":
            raise connect_error(socket.gaierror(11001, "getaddrinfo failed"))
        if request.url.host == "www.d.com":
            return httpx.Response(403, headers={"server": "cloudflare"}, content=challenge)
        return httpx.Response(404, headers=HTML, text="not found")

    result, body, _ = run_fetch(handler)

    assert [a["outcome"] for a in result["attempts"]] == ["dns_fail", "bot_challenge", "http_4xx"]
    assert result["status"] == "failed"
    assert result["error_category"] == "bot_challenge"
    assert result["challenge"] == "cloudflare"
    assert result["chosen_attempt"] == 2
    assert body == challenge  # kept as proof, never analysed


def test_empty_2xx_is_not_a_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=HTML, text="   \n")

    result, _, _ = run_fetch(handler)

    assert result["error_category"] == "empty_response"


def test_large_page_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(static_fetch, "MAX_HTML_BYTES", 10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=HTML, content=b"x" * 100)

    result, body, _ = run_fetch(handler)

    assert body == b"x" * 10
    assert result["html_truncated"] is True
    assert result["html_bytes"] == 10


# ------------------------------------------------------------ end to end, with files


def test_collect_writes_artifacts_with_headers_and_provenance(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers=HTML, text="<html>hello</html>")

    config = Config(raw_dir=tmp_path, accept_language="da-DK,da;q=0.9")
    domain = Domain(row=1, original_input="d.com", display_name="d.com")
    asyncio.run(collect_static([domain], config, factory(handler), FakeSleep()))

    assert seen[0].headers["accept-language"] == "da-DK,da;q=0.9"
    assert seen[0].headers["user-agent"] == config.user_agent

    folder = tmp_path / "d.com"
    html = (folder / "static.html").read_bytes()
    data = json.loads((folder / "static.json").read_text(encoding="utf-8"))
    assert list(data)[0] == "provenance"
    assert data["provenance"]["accept_language"] == "da-DK,da;q=0.9"
    assert data["provenance"]["user_agent"] == config.user_agent
    assert data["html_file"] == "static.html"
    assert data["html_sha256"] == static_fetch.sha256_hex(html)
    assert sorted(p.name for p in folder.iterdir()) == ["static.html", "static.json"]


def test_failed_fetch_removes_stale_html(tmp_path: Path) -> None:
    folder = tmp_path / "d.com"
    folder.mkdir()
    (folder / "static.html").write_bytes(b"old run")

    def handler(request: httpx.Request) -> httpx.Response:
        raise connect_error(socket.gaierror(11001, "getaddrinfo failed"))

    domain = Domain(row=1, original_input="d.com", display_name="d.com")
    config = Config(raw_dir=tmp_path)
    asyncio.run(collect_static([domain], config, factory(handler), FakeSleep()))

    data = json.loads((folder / "static.json").read_text(encoding="utf-8"))
    assert data["error_category"] == "dns_fail"
    assert data["html_file"] is None
    assert not (folder / "static.html").exists()
