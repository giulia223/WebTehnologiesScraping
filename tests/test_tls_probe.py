"""TLS probe tests. A fake connection hands back certificates: no server, no network.

The certificate dicts have the exact shape Python's ssl.SSLSocket.getpeercert() returns.
"""

import asyncio
import json
import ssl
from pathlib import Path
from typing import Any

import pytest

from techdetect.collect.tls_probe import (
    certificate_record,
    collect_tls,
    name_matches,
    names_cover,
    probe_target,
    probe_tls,
)
from techdetect.config import Config
from techdetect.domains import Domain

DER = b"0\x82\x01\x0afake certificate bytes"


def peercert(cn: str, sans: list[str], issuer_o: str = "Let's Encrypt") -> dict[str, Any]:
    return {
        "subject": ((("commonName", cn),),),
        "issuer": (
            (("countryName", "US"),),
            (("organizationName", issuer_o),),
            (("commonName", "R11"),),
        ),
        "version": 3,
        "serialNumber": "04A1B2C3",
        "notBefore": "Sep  1 00:00:00 2026 GMT",
        "notAfter": "Nov 30 00:00:00 2026 GMT",
        "subjectAltName": tuple(("DNS", name) for name in sans),
    }


class FakeConnect:
    """Returns `cert` for a verified connection, or raises `verified_error` instead."""

    def __init__(
        self, cert: dict[str, Any] | None = None, verified_error: Exception | None = None
    ) -> None:
        self.cert = cert
        self.verified_error = verified_error
        self.calls: list[tuple[str, int, bool]] = []

    async def __call__(self, host: str, port: int, verify: bool):
        self.calls.append((host, port, verify))
        if verify and self.verified_error is not None:
            raise self.verified_error
        # Unverified: Python's ssl only gives the raw bytes, never a parsed dict.
        return DER, (self.cert if verify else None)


def write_static(raw_dir: Path, host: str, status: str, final_url: str | None) -> None:
    folder = raw_dir / host
    folder.mkdir(parents=True)
    static = {"domain": host, "status": status, "final_url": final_url}
    (folder / "static.json").write_text(json.dumps(static), encoding="utf-8")


def run_probe(tmp_path: Path, connect: FakeConnect, host: str = "d.com") -> dict[str, Any]:
    return asyncio.run(probe_tls(host, Config(raw_dir=tmp_path), connect))


# ------------------------------------------------------------ name matching


@pytest.mark.parametrize(
    ("host", "pattern", "expected"),
    [
        ("d.com", "d.com", True),
        ("D.COM", "d.com", True),
        ("d.com.", "d.com", True),
        ("www.d.com", "*.d.com", True),
        ("d.com", "*.d.com", False),  # a wildcard needs a label in its place
        ("a.b.d.com", "*.d.com", False),  # and stands for one label only
        ("www.d.com", "w*.d.com", False),  # partial wildcards are not accepted
        ("evil-d.com", "d.com", False),
        ("d.com", "www.d.com", False),
    ],
)
def test_name_matches(host: str, pattern: str, expected: bool) -> None:
    assert name_matches(host, pattern) is expected


def test_names_cover_uses_sans_only() -> None:
    assert names_cover("www.d.com", ["d.com", "*.d.com"])
    assert not names_cover("d.com", [])  # no SANs: the subject CN is not used


# ------------------------------------------------------------ probing


def test_provider_default_certificate_gets_hostname_match_false(tmp_path: Path) -> None:
    # The server answers for d.com with its hosting provider's own certificate.
    cert = peercert("server1.hosting.example", ["server1.hosting.example"], "Hosting Co")
    result = run_probe(tmp_path, FakeConnect(cert))

    assert result["status"] == "ok"
    assert result["chain_verified"] is True
    assert result["hostname_match"] is False
    # Kept on record, so the mismatch can be shown and checked, even though detection
    # will not use it.
    assert result["certificate"]["sans"] == ["server1.hosting.example"]
    assert ["organizationName", "Hosting Co"] in result["certificate"]["issuer"]


def test_matching_certificate_is_parsed(tmp_path: Path) -> None:
    result = run_probe(tmp_path, FakeConnect(peercert("d.com", ["d.com", "*.d.com"])))

    assert result["hostname_match"] is True
    cert = result["certificate"]
    assert cert["subject"] == [["commonName", "d.com"]]
    assert cert["issuer"] == [
        ["countryName", "US"],
        ["organizationName", "Let's Encrypt"],
        ["commonName", "R11"],
    ]
    assert cert["not_before"] == "2026-09-01T00:00:00Z"
    assert cert["not_after"] == "2026-11-30T00:00:00Z"
    assert cert["serial"] == "04A1B2C3"
    assert cert["pem"].startswith("-----BEGIN CERTIFICATE-----")


def test_unverified_certificate_is_kept_as_pem_with_unknown_match(tmp_path: Path) -> None:
    error = ssl.SSLCertVerificationError("certificate verify failed: self-signed certificate")
    connect = FakeConnect(verified_error=error)
    result = run_probe(tmp_path, connect)

    assert connect.calls == [("d.com", 443, True), ("d.com", 443, False)]
    assert result["status"] == "ok"
    assert result["chain_verified"] is False
    assert "self-signed" in result["verify_error"]
    # Python cannot parse an unverified certificate: fields unknown, raw bytes kept.
    assert result["hostname_match"] is None
    assert result["certificate"]["issuer"] is None
    assert result["certificate"]["sans"] is None
    assert ssl.PEM_cert_to_DER_cert(result["certificate"]["pem"]) == DER


def test_connection_refused_is_a_failure_with_category(tmp_path: Path) -> None:
    result = run_probe(tmp_path, FakeConnect(verified_error=ConnectionRefusedError(10061, "no")))

    assert result["status"] == "failed"
    assert result["error_category"] == "conn_refused"
    assert result["certificate"] is None


# ------------------------------------------------------------ which host


def test_probed_host_is_the_final_host_of_a_successful_static_fetch(tmp_path: Path) -> None:
    write_static(tmp_path, "d.com", "ok", "https://www.d.com/en/")
    connect = FakeConnect(peercert("www.d.com", ["www.d.com"]))
    result = run_probe(tmp_path, connect)

    assert connect.calls[0][0] == "www.d.com"
    assert result["probed_host"] == "www.d.com"
    assert result["probed_host_source"] == "static_final_url"
    assert result["hostname_match"] is True


def test_probed_host_is_the_input_host_when_static_failed_or_missing(tmp_path: Path) -> None:
    write_static(tmp_path, "failed.com", "failed", "https://www.failed.com/")
    assert probe_target(tmp_path, "failed.com") == ("failed.com", "input_host")
    assert probe_target(tmp_path, "never-fetched.com") == ("never-fetched.com", "input_host")


def test_certificate_without_san_extension_has_empty_sans() -> None:
    cert = peercert("d.com", [])
    del cert["subjectAltName"]
    assert certificate_record(DER, cert)["sans"] == []


def test_collect_writes_tls_json_with_provenance(tmp_path: Path) -> None:
    config = Config(raw_dir=tmp_path)
    connect = FakeConnect(peercert("d.com", ["d.com"]))

    asyncio.run(collect_tls([Domain(1, "d.com", "d.com")], config, connect))

    saved = json.loads((tmp_path / "d.com" / "tls.json").read_text(encoding="utf-8"))
    assert saved["provenance"]["crawl_location"] == config.crawl_location
    assert saved["hostname_match"] is True
    assert not list(tmp_path.rglob("*.tmp"))
