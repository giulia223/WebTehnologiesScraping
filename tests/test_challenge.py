from pathlib import Path

import pytest

from techdetect.collect.challenge import detect_challenge, is_dead_behind_cdn

# Hand-written pages modelled on each vendor's challenge, not captured real pages.
FIXTURES = Path(__file__).parent / "fixtures" / "challenges"


def page(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.mark.parametrize(
    ("fixture", "status", "headers", "vendor"),
    [
        ("cloudflare.html", 403, [("server", "cloudflare")], "cloudflare"),
        ("datadome.html", 403, [("x-datadome", "protected")], "datadome"),
        ("akamai.html", 403, [("server", "AkamaiGHost")], "akamai"),
    ],
)
def test_challenge_pages_are_recognised(
    fixture: str, status: int, headers: list[tuple[str, str]], vendor: str
) -> None:
    assert detect_challenge(status, headers, page(fixture)) == vendor


def test_cloudflare_header_alone_is_enough() -> None:
    headers = [("cf-mitigated", "challenge")]
    assert detect_challenge(403, headers, b"<html></html>") == "cloudflare"


def test_normal_page_with_cloudflare_script_is_not_a_challenge() -> None:
    # Cloudflare injects this script into ordinary pages; it must not count as a wall.
    body = page("normal_cloudflare_site.html")
    assert detect_challenge(200, [("server", "cloudflare")], body) is None


def test_plain_access_denied_without_akamai_is_not_a_challenge() -> None:
    # A site's own "Access Denied" page (no Akamai reference code, no Akamai header) is an
    # HTTP error, not a bot wall.
    body = b"<html><head><title>Access Denied</title></head><body>Members only.</body></html>"
    assert detect_challenge(403, [("server", "nginx")], body) is None


# Shaped like the real block pages in the collected data (ecolab.com, stwannas.com...).
AKAMAI_DENY = (
    b"<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY><H1>Access Denied</H1>"
    b'You don\'t have permission to access "http&#58;&#47;&#47;www&#46;example&#46;com&#47;"'
    b" on this server.<P>Reference&#32;&#35;18&#46;36496768&#46;1794948661&#46;2a2c3f0</BODY>"
)
CLOUDFLARE_BLOCK = (
    b"<!DOCTYPE html><html><head><title>Attention Required! | Cloudflare</title></head>"
    b"<body><h1>Sorry, you have been blocked</h1></body></html>"
)


def test_akamai_deny_page_is_recognised_by_its_text_without_a_server_header() -> None:
    assert detect_challenge(403, [], AKAMAI_DENY) == "akamai"


def test_akamai_grn_header_marks_an_access_denied_page() -> None:
    body = b"<html><head><title>Access Denied</title></head><body>Access Denied</body></html>"
    assert detect_challenge(403, [("akamai-grn", "0.a73bd417.1794948661")], body) == "akamai"


def test_cloudflare_block_page_is_recognised() -> None:
    assert detect_challenge(403, [("server", "cloudflare")], CLOUDFLARE_BLOCK) == "cloudflare"


def test_a_plain_403_says_nothing_about_who_blocked() -> None:
    body = b"<html><head><title>403 Forbidden</title></head><body>403 Forbidden</body></html>"
    assert detect_challenge(403, [("server", "awselb/2.0")], body) is None


def test_cloudflare_dns_resolution_error_means_a_dead_site() -> None:
    body = b"<html><head><title>DNS resolution error | x.com | Cloudflare</title></head></html>"
    assert is_dead_behind_cdn(409, body)
    assert not is_dead_behind_cdn(200, b"<title>Our shop</title> cloudflare")
