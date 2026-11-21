import importlib.util
from pathlib import Path

import pytest

from techdetect.domains import load_domains, registered_domain, to_display_name, validate_hostname

REPO = Path(__file__).resolve().parent.parent
INPUT = REPO / "data" / "input" / "domains.parquet"


@pytest.mark.parametrize(
    ("value", "reason_contains"),
    [
        ("HTTPS://x.com", "uppercase"),
        ("https://x.com", "outside a-z 0-9 -"),
        ("a..b", "empty label"),
        ("-x.com", "hyphen"),
        ("a" * 64 + ".com", "longer than 63"),
        ("xn--a.com", "not valid punycode"),
        ("", "empty value"),
        ("x.com.", "empty label"),
        (" x.com", "outside a-z 0-9 -"),
        ("køleoghvidevareservice.dk", "non-ASCII"),
        (".".join(["a" * 60] * 5), "longer than 253"),
        ("localhost", "two labels"),
    ],
)
def test_invalid_hostnames_are_rejected(value: str, reason_contains: str) -> None:
    reason = validate_hostname(value)
    assert reason is not None and reason_contains in reason


@pytest.mark.parametrize(
    "value", ["example.com", "3xd.co.uk", "den-a-sha.co.jp", "xn--jobvrkstedet-9cb.nu"]
)
def test_valid_hostnames_pass(value: str) -> None:
    assert validate_hostname(value) is None


def test_display_name_decodes_punycode() -> None:
    assert to_display_name("xn--jobvrkstedet-9cb.nu") == "jobværkstedet.nu"
    assert to_display_name("xn--kleoghvidevareservice-qfc.dk") == "køleoghvidevareservice.dk"
    assert to_display_name("example.com") == "example.com"


def test_invalid_rows_are_reported_not_fixed(tmp_path: Path) -> None:
    path = tmp_path / "mixed.txt"
    path.write_text("good.com\nHTTPS://x.com\na..b\n-x.com\nother.org\n", encoding="utf-8")

    result = load_domains(path)

    assert [d.original_input for d in result.domains] == ["good.com", "other.org"]
    assert [d.row for d in result.domains] == [1, 5]
    # Reported with the exact original value: nothing lowercased, stripped or dropped.
    assert [(bad.row, bad.value) for bad in result.invalid] == [
        (2, "HTTPS://x.com"),
        (3, "a..b"),
        (4, "-x.com"),
    ]


def test_csv_header_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "list.csv"
    # Bytes, not write_text: on Windows write_text would turn "\n" into "\r\n" again.
    path.write_bytes(b"root_domain\r\nexample.com\r\nxn--jobvrkstedet-9cb.nu\r\n")

    result = load_domains(path)

    assert result.invalid == []
    assert [d.display_name for d in result.domains] == ["example.com", "jobværkstedet.nu"]


def test_unsupported_file_type(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported file type"):
        load_domains(path)


def test_real_input_loads_cleanly() -> None:
    result = load_domains(INPUT)

    assert len(result.domains) == 200
    assert result.invalid == []
    by_row = {d.row: d.host for d in result.domains}
    # Row numbers are 1-based and match the blind-label sample (every 20th row).
    assert by_row[20] == "clotting-guide.online"
    assert by_row[200] == "rivithead.com"


def _load_inspect_script():  # scripts/ is not a package, so import it by path
    spec = importlib.util.spec_from_file_location(
        "inspect_input", REPO / "scripts" / "inspect_input.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inspect_script_tldextract_works_offline() -> None:
    # The autouse no_network fixture makes any download attempt fail this test.
    script = _load_inspect_script()

    [(host, sub, domain, suffix, private)] = script.split_rows(["kande-hoikuen.ed.jp"])

    assert (sub, domain, suffix, private) == ("", "kande-hoikuen", "ed.jp", False)
    assert script.is_public_suffix("co.uk")


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("static.example.co.uk", "example.co.uk"),  # a two-label public suffix
        ("WWW.Example.com.", "example.com"),  # case and the trailing dot do not matter
        ("x.weebly.com", "weebly.com"),  # platform hosts are not private suffixes (bundled list)
        ("localhost", "localhost"),  # no known suffix: returned unchanged
    ],
)
def test_registered_domain_offline(host: str, expected: str) -> None:
    assert registered_domain(host) == expected
