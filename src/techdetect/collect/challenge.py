"""Recognise bot-protection pages ("checking your browser", "access denied") from vendors.

A challenge or block page is the vendor's page, not the site: analysing it would credit
the site with the vendor's scripts and miss everything the real page uses. So it never
counts as a successful fetch, but it is kept on disk as proof of why the site could not
be seen.

Signals are deliberately strict. Cloudflare, for example, injects its
`/cdn-cgi/challenge-platform/` script into many *normal* pages, so that script alone
must not mark a page as a challenge. A plain "403 Forbidden" says nothing about who
blocked us, so it stays an HTTP error.
"""

import re
from collections.abc import Sequence

# Akamai's standard deny page ends with "Reference #18.<hex>..." (sometimes written with
# HTML entities: "Reference&#32;&#35;18&#46;...").
_AKAMAI_REFERENCE = re.compile(r"reference(?:\s|&#32;)*(?:#|&#35;)\d+(?:\.|&#46;)[0-9a-f]")


def _header(headers: Sequence[tuple[str, str]], name: str) -> str:
    """All values of one header, joined (names compared case-insensitively)."""
    return " ".join(value for key, value in headers if key.lower() == name).lower()


def detect_challenge(status: int, headers: Sequence[tuple[str, str]], body: bytes) -> str | None:
    """Return the vendor name ("cloudflare", "datadome", "akamai") or None."""
    text = body.decode("utf-8", errors="replace").lower()
    server = _header(headers, "server")

    # Cloudflare marks its challenge responses with this header; the page itself has the
    # "Just a moment..." title and a _cf_chl_opt settings object.
    if "challenge" in _header(headers, "cf-mitigated"):
        return "cloudflare"
    if "<title>just a moment...</title>" in text or "_cf_chl_opt" in text:
        return "cloudflare"
    # Cloudflare's block page ("Sorry, you have been blocked"): a 403 titled
    # "Attention Required! | Cloudflare".
    if status == 403 and "attention required! | cloudflare" in text:
        return "cloudflare"

    # DataDome serves its captcha from captcha-delivery.com.
    if "captcha-delivery.com" in text:
        return "datadome"
    if status == 403 and _header(headers, "x-datadome"):
        return "datadome"

    # Akamai Bot Manager: its interactive challenge path, or its "Access Denied" page.
    # The page is recognised by its own text (the "Reference #18...." code), since many
    # of these responses send no `server: AkamaiGHost` header; `akamai-grn` is Akamai's too.
    if "/_sec/cp_challenge/" in text:
        return "akamai"
    if status == 403 and "<title>access denied</title>" in text:
        if (
            "akamaighost" in server
            or _header(headers, "akamai-grn")
            or "errors.edgesuite.net" in text
            or _AKAMAI_REFERENCE.search(text)
        ):
            return "akamai"

    return None


def is_dead_behind_cdn(status: int, body: bytes) -> bool:
    """Cloudflare answering for a site whose origin name no longer resolves (its error
    1001, "DNS resolution error"): the CDN is there, the website behind it is gone."""
    text = body.decode("utf-8", errors="replace").lower()
    return status >= 400 and "<title>dns resolution error" in text and "cloudflare" in text
