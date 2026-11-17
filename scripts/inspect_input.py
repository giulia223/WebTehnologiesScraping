"""Print a profile of the input domain list: schema, cleanliness counts, label/TLD mix,
and how tldextract splits the multi-label rows.

Report only: nothing in the tool depends on this output. Works offline: tldextract is
built with its bundled Public Suffix List snapshot and never downloads a fresh one.

Usage: python scripts/inspect_input.py [path]   (default: data/input/domains.parquet)
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq
import tldextract

from techdetect.console import use_utf8_output
from techdetect.domains import COLUMN, load_domains, to_display_name

DEFAULT_PATH = Path("data/input/domains.parquet")

# Multi-part endings we expect to be public suffixes (like co.uk), to confirm explicitly.
SUFFIXES_TO_CHECK = ["ed.jp", "edu.hu", "info.pl"]

# The 9 rows that look like hosts on someone else's platform rather than root domains.
PLATFORM_HOSTS = [
    "5starremoval.weebly.com",
    "1planettechnologies.weebly.com",
    "5rcircleprocess.weebly.com",
    "chavchavadze-m2-41-tbilisi.booked.net",
    "la-residenza-del-principe-rome-it.booked.net",
    "hansis-knusperhendl-foodtruck.eatbu.com",
    "concrete-fitness.edan.io",
    "joseph-herber.business.site",
    "insieme.stanford.edu",
]


def offline_extractors() -> tuple[tldextract.TLDExtract, tldextract.TLDExtract]:
    """Two extractors on the bundled snapshot: ICANN suffixes only, and ICANN + private.

    suffix_list_urls=() means "never fetch the list"; cache_dir=None means "don't read or
    write a cached copy", so the result depends only on the installed tldextract version.
    """
    icann = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
    with_private = tldextract.TLDExtract(
        suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True
    )
    return icann, with_private


def cleanliness_counts(values: list[str]) -> dict[str, int]:
    """The checks a "fix it" loader would have needed. Non-zero means the file is not clean."""
    lowered = [v.lower() for v in values]
    return {
        "has a scheme (://)": sum("://" in v for v in values),
        "starts with www.": sum(v.startswith("www.") for v in lowered),
        "has a path (/)": sum("/" in v for v in values),
        "has a port (:)": sum(":" in v for v in values),
        "uppercase letters": sum(v != v.lower() for v in values),
        "whitespace": sum(any(c.isspace() for c in v) for v in values),
        "trailing dot": sum(v.endswith(".") for v in values),
        "empty": sum(v == "" for v in values),
        "duplicates (after lowercasing)": len(lowered) - len(set(lowered)),
        "non-ASCII characters": sum(not v.isascii() for v in values),
    }


def print_schema(path: Path) -> None:
    parquet = pq.ParquetFile(path)
    print("== Schema ==")
    print(parquet.schema_arrow)
    print(f"written by: {parquet.metadata.created_by}")
    spark = (parquet.schema_arrow.metadata or {}).get(b"org.apache.spark.version")
    if spark:
        print(f"Spark version: {spark.decode()}")


def print_counts(values: list[str]) -> None:
    print("\n== Rows ==")
    print(f"rows: {len(values)}, distinct: {len(set(values))}")

    print("\n== Cleanliness (rows affected) ==")
    for check, count in cleanliness_counts(values).items():
        print(f"{check:32} {count}")

    print("\n== Punycode (international names) ==")
    for v in values:
        if "xn--" in v:
            print(f"{v}  ->  {to_display_name(v)}")

    print("\n== Labels per name ==")
    for n, count in sorted(Counter(v.count(".") + 1 for v in values).items()):
        print(f"{n} labels: {count}")

    tlds = Counter(v.rsplit(".", 1)[-1] for v in values)
    print(f"\n== Last label (TLD): {len(tlds)} different ==")
    print(", ".join(f".{tld} {count}" for tld, count in tlds.most_common()))


def split_rows(hosts: list[str]) -> list[tuple[str, str, str, str, bool]]:
    """(host, subdomain, domain, suffix, suffix_is_private) per host.

    subdomain/domain/suffix come from ICANN suffixes only (what a registrar sells);
    suffix_is_private says whether the private part of the list (platforms that
    hand out subdomains, e.g. github.io) would have given a longer suffix.
    """
    icann, with_private = offline_extractors()
    rows = []
    for host in hosts:
        parts = icann(host)
        rows.append(
            (host, parts.subdomain, parts.domain, parts.suffix, with_private(host).is_private)
        )
    return rows


def is_public_suffix(suffix: str) -> bool:
    """True if the bundled list has `suffix` as one ICANN public suffix."""
    icann, _ = offline_extractors()
    return icann(f"example.{suffix}").suffix == suffix


def print_tldextract_report(values: list[str]) -> None:
    print(f"\n== tldextract {tldextract.__version__}, bundled snapshot, no download ==")

    three_labels = [v for v in values if v.count(".") == 2]
    print(f"\n-- How the {len(three_labels)} three-label rows split (subdomain | domain | suffix)")
    for host, sub, domain, suffix, _ in split_rows(three_labels):
        print(f"{host:46} {sub or '-':32} | {domain:32} | {suffix}")

    print("\n-- Expected two-part public suffixes")
    for suffix in SUFFIXES_TO_CHECK:
        answer = "yes" if is_public_suffix(suffix) else "NO"
        print(f"{suffix:10} is a public suffix: {answer}")

    print("\n-- Platform hosts: is the suffix on the PRIVATE part of the list?")
    for host, _, _, _, private in split_rows(PLATFORM_HOSTS):
        print(f"{host:46} {'yes' if private else 'no'}")


def main() -> None:
    use_utf8_output()
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    values = pq.read_table(path, columns=[COLUMN]).column(COLUMN).to_pylist()

    print_schema(path)
    print_counts(values)

    loaded = load_domains(path)
    print(f"\n== Loader: {len(loaded.domains)} valid, {len(loaded.invalid)} invalid ==")
    for bad in loaded.invalid:
        print(f"row {bad.row}: {bad.value!r}: {bad.reason}")

    print_tldextract_report(values)


if __name__ == "__main__":
    main()
