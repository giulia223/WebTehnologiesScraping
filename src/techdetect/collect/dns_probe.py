"""DNS probe: the public DNS records of each input host, written to data/raw/<domain>/dns.json.

DNS is a cheap signal that needs no page: MX names the email provider, NS the DNS host,
TXT holds SPF senders and site-verification tokens, a www CNAME often names the CDN or
hosting platform. Every query uses the input host exactly as given, never a computed
parent: for x.weebly.com we ask about x.weebly.com, not weebly.com, because the parent's
records describe the platform, not this site.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import dns.asyncresolver
import dns.exception
import dns.rdatatype
import dns.resolver

from techdetect.collect.provenance import provenance_header
from techdetect.collect.runner import run_per_domain
from techdetect.collect.storage import domain_dir, write_json_atomic
from techdetect.config import Config
from techdetect.domains import Domain

# A query function: (name, record type) -> dnspython Answer. Replaceable so tests can
# answer from fixed records instead of the network.
Resolve = Callable[[str, str], Awaitable[dns.resolver.Answer]]


def planned_queries(host: str) -> list[tuple[str, str]]:
    """The (name, record type) pairs asked for one host."""
    www = f"www.{host}"
    return [
        (host, "A"),
        (host, "AAAA"),
        (host, "CNAME"),
        (www, "A"),
        (www, "AAAA"),
        (www, "CNAME"),
        (host, "MX"),
        (host, "NS"),
        (host, "TXT"),
        # The zone's primary name server and admin mailbox, often naming the DNS host;
        # webappanalyzer has rules on it. A subdomain without its own zone: no_answer.
        (host, "SOA"),
        # DMARC policy lives on a fixed subdomain; it can name a mail-security vendor.
        (f"_dmarc.{host}", "TXT"),
    ]


def _record_text(rdata: Any) -> str:
    if rdata.rdtype == dns.rdatatype.TXT:
        # One TXT record can be split into several strings of at most 255 bytes; a long
        # SPF record would otherwise be cut in two. Joined, it is the text the owner wrote.
        return b"".join(rdata.strings).decode("utf-8", errors="replace")
    return rdata.to_text()


def _status(exc: Exception) -> str:
    if isinstance(exc, dns.resolver.NXDOMAIN):
        return "nxdomain"  # the name does not exist at all
    if isinstance(exc, dns.resolver.NoAnswer):
        return "no_answer"  # the name exists, but has no record of this type
    if isinstance(exc, dns.exception.Timeout):
        return "timeout"
    if isinstance(exc, dns.resolver.NoNameservers):
        return "no_nameservers"  # every server failed to answer (e.g. SERVFAIL)
    return "error"


async def run_query(resolve: Resolve, name: str, rdtype: str) -> dict[str, Any]:
    """One query, turned into a JSON record. Failures are recorded, never raised."""
    record: dict[str, Any] = {"name": name, "type": rdtype, "status": "ok", "ttl": None}
    try:
        answer = await resolve(name, rdtype)
    except (dns.exception.DNSException, OSError) as exc:
        record.update(status=_status(exc), records=[], error=f"{type(exc).__name__}: {exc}")
        return record
    record["ttl"] = answer.rrset.ttl if answer.rrset is not None else None
    record["records"] = [_record_text(rdata) for rdata in answer]
    if rdtype in ("A", "AAAA"):
        # After following CNAMEs: the name that finally holds the address (often the CDN's).
        record["canonical_name"] = answer.canonical_name.to_text()
    return record


def system_resolver(timeout_s: float) -> tuple[Resolve, list[str]]:
    """The machine's configured DNS servers, and their addresses for the record."""
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = timeout_s  # total time per query, across retries and servers

    async def resolve(name: str, rdtype: str) -> dns.resolver.Answer:
        # search=False: never append the local search domain (e.g. "home") to the name.
        return await resolver.resolve(name, rdtype, search=False)

    return resolve, [str(ns) for ns in resolver.nameservers]


async def probe_dns(
    host: str, config: Config, resolve: Resolve, nameservers: list[str]
) -> dict[str, Any]:
    """All planned queries for one host, run at the same time; the dns.json content."""
    provenance = provenance_header(config)
    start = time.perf_counter()
    # The small queries go out together: one domain takes as long as its slowest query.
    records = await asyncio.gather(
        *(run_query(resolve, name, rdtype) for name, rdtype in planned_queries(host))
    )
    return {
        "provenance": provenance,
        "domain": host,
        # "ok" = the name resolved to something; "failed" = no query got an answer.
        "status": "ok" if any(r["status"] == "ok" for r in records) else "failed",
        "resolver": {"nameservers": nameservers, "timeout_s": config.dns_timeout_s},
        "elapsed_ms": round((time.perf_counter() - start) * 1000),
        "queries": list(records),
    }


async def collect_dns(
    domains: list[Domain],
    config: Config,
    resolver: tuple[Resolve, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Probe and save every domain, at most config.concurrency domains at a time."""
    resolve, nameservers = resolver or system_resolver(config.dns_timeout_s)

    async def one(domain: Domain) -> dict[str, Any]:
        result = await probe_dns(domain.host, config, resolve, nameservers)
        write_json_atomic(domain_dir(config.raw_dir, domain.host) / "dns.json", result)
        return result

    return await run_per_domain(domains, config.concurrency, one)
