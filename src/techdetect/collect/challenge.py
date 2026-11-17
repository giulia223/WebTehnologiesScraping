"""Recognise bot-challenge pages ("checking your browser") from anti-bot vendors.

A challenge page is the vendor's page, not the site: analysing it would credit the site
with the vendor's scripts and miss everything the real page uses. So it never counts as
a successful fetch, but it is kept on disk as proof of why the site could not be seen.

Signals are deliberately strict. Cloudflare, for example, injects its
`/cdn-cgi/challenge-platform/` script into many *normal* pages, so that script alone
must not mark a page as a challenge.
"""

from collections.abc import Sequence


def _header(headers: Sequence[tuple[str, str]], name: str) -> str:
    """All values of one header, joined (names compared case-insensitively)."""
    return " ".join(value for key, value in headers if key.lower() == name).lower()


def detect_challenge(
    status: int, headers: Sequence[tuple[str, str]], body: bytes
) -> str | None:
    """Return the vendor name ("cloudflare", "datadome", "akamai") or None."""
    text = body.decode("utf-8", errors="replace").lower()
    server = _header(headers, "server")

    # Cloudflare marks its challenge responses with this header; the page itself has the
    # "Just a moment..." title and a _cf_chl_opt settings object.
    if "challenge" in _header(headers, "cf-mitigated"):
        return "cloudflare"
    if "<title>just a moment...</title>" in text or "_cf_chl_opt" in text:
        return "cloudflare"

    # DataDome serves its captcha from captcha-delivery.com.
    if "captcha-delivery.com" in text:
        return "datadome"
    if status == 403 and _header(headers, "x-datadome"):
        return "datadome"

    # Akamai Bot Manager: an "Access Denied" page from its edge servers, or its
    # interactive challenge path.
    if "/_sec/cp_challenge/" in text:
        return "akamai"
    if status == 403 and "akamaighost" in server and "access denied" in text:
        return "akamai"

    return None
