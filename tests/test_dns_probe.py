"""DNS probe tests. A fake resolver answers from fixed records written as DNS text: no network.

The fake returns real dnspython Answer objects and raises real dnspython exceptions, so
the conversion from dnspython to JSON is tested too, not only our own code.
"""

import asyncio
import json
from pathlib import Path

import dns.message
import dns.rdataclass
import dns.rdatatype
import dns.resolver

from techdetect.collect.dns_probe import collect_dns, planned_queries, probe_dns
from techdetect.config import Config
from techdetect.domains import Domain


def answer(qname: str, rdtype: str, *answer_lines: str) -> dns.resolver.Answer:
    """A dnspython Answer built from zone-file lines, as a DNS server would send them."""
    text = "\n".join(
        ["id 1", "flags QR RD RA", ";QUESTION", f"{qname}. IN {rdtype}", ";ANSWER", *answer_lines]
    )
    response = dns.message.from_text(text)
    return dns.resolver.Answer(
        response.question[0].name,
        dns.rdatatype.from_text(rdtype),
        dns.rdataclass.IN,
        response,
    )


class FakeResolver:
    """Answers from a {(name, type): Answer or exception} table. Missing = NoAnswer."""

    def __init__(self, table: dict[tuple[str, str], object]) -> None:
        self.table = table
        self.asked: list[tuple[str, str]] = []

    async def __call__(self, name: str, rdtype: str) -> dns.resolver.Answer:
        self.asked.append((name, rdtype))
        found = self.table.get((name, rdtype), dns.resolver.NoAnswer())
        if isinstance(found, Exception):
            raise found
        return found


def probe(table: dict[tuple[str, str], object], host: str) -> tuple[dict, FakeResolver]:
    resolver = FakeResolver(table)
    result = asyncio.run(probe_dns(host, Config(), resolver, ["192.0.2.53"]))
    return result, resolver


def query(result: dict, name: str, rdtype: str) -> dict:
    return next(q for q in result["queries"] if q["name"] == name and q["type"] == rdtype)


def test_platform_subdomain_is_queried_exactly_never_its_parent() -> None:
    result, resolver = probe({}, "x.weebly.com")

    expected = {
        ("x.weebly.com", "A"),
        ("x.weebly.com", "AAAA"),
        ("x.weebly.com", "CNAME"),
        ("www.x.weebly.com", "A"),
        ("www.x.weebly.com", "AAAA"),
        ("www.x.weebly.com", "CNAME"),
        ("x.weebly.com", "MX"),
        ("x.weebly.com", "NS"),
        ("x.weebly.com", "TXT"),
        ("_dmarc.x.weebly.com", "TXT"),
    }
    assert set(resolver.asked) == expected
    assert len(resolver.asked) == len(expected) == len(result["queries"])
    assert not any(name == "weebly.com" for name, _ in resolver.asked)
    assert set(planned_queries("x.weebly.com")) == expected


def test_records_ttl_and_cname_chain() -> None:
    table = {
        ("www.d.com", "A"): answer(
            "www.d.com",
            "A",
            "www.d.com. 300 IN CNAME d.cdn.example.net.",
            "d.cdn.example.net. 60 IN A 192.0.2.10",
        ),
        ("d.com", "MX"): answer(
            "d.com", "MX", "d.com. 3600 IN MX 1 aspmx.l.google.com.", "d.com. 3600 IN MX 5 alt1.x."
        ),
    }
    result, _ = probe(table, "d.com")

    www_a = query(result, "www.d.com", "A")
    assert www_a["status"] == "ok"
    assert www_a["records"] == ["192.0.2.10"]
    # The name that finally holds the address: here the CDN's, through the CNAME.
    assert www_a["canonical_name"] == "d.cdn.example.net."
    mx = query(result, "d.com", "MX")
    assert mx["records"] == ["1 aspmx.l.google.com.", "5 alt1.x."]
    assert mx["ttl"] == 3600
    assert "canonical_name" not in mx
    assert result["status"] == "ok"


def test_txt_split_into_255_byte_strings_is_joined() -> None:
    long_spf = "v=spf1 " + "include:_spf.example.net " * 12 + "~all"
    first, second = long_spf[:255], long_spf[255:]
    table = {("d.com", "TXT"): answer("d.com", "TXT", f'd.com. 300 IN TXT "{first}" "{second}"')}
    result, _ = probe(table, "d.com")

    assert query(result, "d.com", "TXT")["records"] == [long_spf]


def test_failures_are_recorded_with_their_own_status() -> None:
    table = {
        ("_dmarc.d.com", "TXT"): dns.resolver.NXDOMAIN(),
        ("d.com", "MX"): dns.resolver.LifetimeTimeout(timeout=5.0, errors=[]),
        ("d.com", "NS"): dns.resolver.NoNameservers(),
        ("d.com", "A"): answer("d.com", "A", "d.com. 300 IN A 192.0.2.1"),
    }
    result, _ = probe(table, "d.com")

    assert query(result, "_dmarc.d.com", "TXT")["status"] == "nxdomain"
    assert query(result, "d.com", "MX")["status"] == "timeout"
    assert query(result, "d.com", "NS")["status"] == "no_nameservers"
    no_answer = query(result, "d.com", "CNAME")  # not in the table: NoAnswer
    assert no_answer["status"] == "no_answer"
    assert no_answer["records"] == []
    assert no_answer["error"].startswith("NoAnswer")


def test_a_name_that_resolves_nothing_is_failed() -> None:
    result, _ = probe({}, "gone.example")
    assert result["status"] == "failed"


def test_collect_writes_dns_json_with_provenance(tmp_path: Path) -> None:
    config = Config(raw_dir=tmp_path)
    table = {("d.com", "A"): answer("d.com", "A", "d.com. 300 IN A 192.0.2.1")}
    resolver = FakeResolver(table)

    asyncio.run(collect_dns([Domain(1, "d.com", "d.com")], config, (resolver, ["192.0.2.53"])))

    saved = json.loads((tmp_path / "d.com" / "dns.json").read_text(encoding="utf-8"))
    assert saved["provenance"]["crawl_location"] == config.crawl_location
    assert saved["resolver"] == {"nameservers": ["192.0.2.53"], "timeout_s": config.dns_timeout_s}
    assert query(saved, "d.com", "A")["records"] == ["192.0.2.1"]
    assert not list(tmp_path.rglob("*.tmp"))
