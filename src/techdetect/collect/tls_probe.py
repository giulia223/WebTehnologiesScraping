"""TLS probe: the certificate a site serves, written to data/raw/<domain>/tls.json.

The certificate's issuer can name the certificate authority or the platform (Let's
Encrypt, Cloudflare, a hosting company). It is read from the host of the *final* URL of
the static fetch (after redirects), since that is the server that actually served the
page; if the static fetch failed, from the input host.

A server can answer with a certificate for other names (e.g. the hosting provider's
default certificate). It is kept, with `hostname_match: false`, so detection can skip it:
it describes the provider's machine, not this site.
"""

import asyncio
import ssl
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from techdetect.collect.provenance import provenance_header
from techdetect.collect.runner import run_per_domain
from techdetect.collect.static_fetch import classify_error, describe_error, static_final_url
from techdetect.collect.storage import domain_dir, sha256_hex, write_json_atomic
from techdetect.config import Config
from techdetect.domains import Domain

PORT = 443
CLOSE_TIMEOUT_S = 2.0

# (host, port, verify) -> (certificate as DER bytes, parsed certificate or None).
# Replaceable so tests can hand back any certificate without a server.
Connect = Callable[[str, int, bool], Awaitable[tuple[bytes, dict[str, Any] | None]]]


# ---------------------------------------------------------------- name matching


def name_matches(host: str, pattern: str) -> bool:
    """Does one certificate name cover `host`? Case-insensitive, trailing dot ignored.

    A wildcard stands for exactly one leftmost label: *.x.com covers a.x.com, but not
    x.com itself nor a.b.x.com. Partial wildcards (f*.x.com) are not accepted, as in
    current browsers.
    """
    host = host.lower().rstrip(".")
    pattern = pattern.lower().rstrip(".")
    if pattern.startswith("*."):
        first_label, _, rest = host.partition(".")
        return bool(first_label) and rest == pattern[2:]
    return host == pattern


def names_cover(host: str, sans: list[str]) -> bool:
    """Only the subjectAltName DNS names count, like in current browsers; the subject CN
    is recorded but not used, since browsers stopped trusting it for this check."""
    return any(name_matches(host, name) for name in sans)


# ---------------------------------------------------------------- parsing


def _name_pairs(rdns: Any) -> list[list[str]]:
    # Pairs, not a dict: an attribute such as organizationalUnitName can repeat.
    return [[key, value] for rdn in rdns for key, value in rdn]


def _iso(cert_time: str | None) -> str | None:
    if not cert_time:
        return None
    seconds = ssl.cert_time_to_seconds(cert_time)  # e.g. "Jun  1 12:00:00 2026 GMT"
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def certificate_record(der: bytes, parsed: dict[str, Any] | None) -> dict[str, Any]:
    """The certificate for tls.json.

    `parsed` is what Python's ssl module gives (getpeercert()), which it only does for a
    certificate that passed verification. For an unverified one only the raw bytes exist;
    they are kept as PEM so a later parser can read them without probing the site again.
    """
    parsed = parsed or {}
    return {
        "subject": _name_pairs(parsed["subject"]) if "subject" in parsed else None,
        "issuer": _name_pairs(parsed["issuer"]) if "issuer" in parsed else None,
        "sans": (
            [value for kind, value in parsed["subjectAltName"] if kind == "DNS"]
            if "subjectAltName" in parsed
            else ([] if parsed else None)
        ),
        "not_before": _iso(parsed.get("notBefore")),
        "not_after": _iso(parsed.get("notAfter")),
        "serial": parsed.get("serialNumber"),
        "sha256": sha256_hex(der),
        "pem": ssl.DER_cert_to_PEM_cert(der),
    }


# ---------------------------------------------------------------- connecting


def default_connect(timeout_s: float) -> Connect:
    """Real TLS connections, with the same trusted certificate authorities as httpx."""
    verified = httpx.create_ssl_context()
    # The chain is still verified, but a name mismatch must not abort the handshake: the
    # mismatching certificate is exactly what we want to record (hostname_match: false).
    verified.check_hostname = False
    unverified = httpx.create_ssl_context()
    unverified.check_hostname = False
    unverified.verify_mode = ssl.CERT_NONE

    async def connect(host: str, port: int, verify: bool) -> tuple[bytes, dict[str, Any] | None]:
        async with asyncio.timeout(timeout_s):
            _, writer = await asyncio.open_connection(
                host, port, ssl=verified if verify else unverified, server_hostname=host
            )
        try:
            tls = writer.get_extra_info("ssl_object")
            der = tls.getpeercert(binary_form=True)
            # Without verification Python returns an empty dict here, so don't ask.
            parsed = tls.getpeercert() if verify else None
        finally:
            writer.close()
            # A server that never acknowledges the close must not hold the probe.
            with suppress(OSError, TimeoutError):
                async with asyncio.timeout(CLOSE_TIMEOUT_S):
                    await writer.wait_closed()
        return der, parsed

    return connect


def probe_target(raw_dir: Path, host: str) -> tuple[str, str]:
    """Which host to probe, and why: the static fetch's final host, else the input host."""
    final_url = static_final_url(raw_dir, host)
    final_host = urlsplit(final_url).hostname if final_url else None
    if final_host:
        return final_host, "static_final_url"
    return host, "input_host"


async def probe_tls(host: str, config: Config, connect: Connect) -> dict[str, Any]:
    """Probe one domain; the tls.json content."""
    provenance = provenance_header(config)
    start = time.perf_counter()
    probed_host, source = probe_target(config.raw_dir, host)
    result: dict[str, Any] = {
        "provenance": provenance,
        "domain": host,
        "probed_host": probed_host,
        "probed_host_source": source,
        "port": PORT,
        "status": "ok",
        "error_category": None,
        "error": None,
        "chain_verified": True,
        "verify_error": None,
        "hostname_match": None,
        "certificate": None,
    }
    try:
        try:
            der, parsed = await connect(probed_host, PORT, True)
        except ssl.SSLCertVerificationError as exc:
            # Self-signed, expired or unknown authority: look anyway and record why the
            # certificate was not trusted. The certificate itself is a finding.
            result["chain_verified"] = False
            result["verify_error"] = describe_error(exc)
            der, parsed = await connect(probed_host, PORT, False)
    except (OSError, TimeoutError) as exc:  # ssl.SSLError is an OSError too
        result.update(
            status="failed", error_category=classify_error(exc), error=describe_error(exc)
        )
    else:
        result["certificate"] = certificate_record(der, parsed)
        sans = result["certificate"]["sans"]
        # Unknown (None) when the certificate could not be parsed: unverified.
        result["hostname_match"] = names_cover(probed_host, sans) if sans is not None else None
    result["elapsed_ms"] = round((time.perf_counter() - start) * 1000)
    return result


async def collect_tls(
    domains: list[Domain], config: Config, connect: Connect | None = None
) -> list[dict[str, Any]]:
    """Probe and save every domain, at most config.concurrency at a time."""
    # Same budget as the static fetch: both wait on the same kind of server.
    connect = connect or default_connect(config.static_timeout_s)

    async def one(domain: Domain) -> dict[str, Any]:
        result = await probe_tls(domain.host, config, connect)
        write_json_atomic(domain_dir(config.raw_dir, domain.host) / "tls.json", result)
        return result

    return await run_per_domain(domains, config.concurrency, one)
