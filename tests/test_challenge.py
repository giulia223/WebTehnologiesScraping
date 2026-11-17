from pathlib import Path

import pytest

from techdetect.collect.challenge import detect_challenge

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
    # A site's own 403 page is an HTTP error, not a bot wall.
    assert detect_challenge(403, [("server", "nginx")], page("akamai.html")) is None
