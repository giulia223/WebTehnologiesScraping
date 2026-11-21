"""Load the input domain list: validate every row, never modify one.

The input is already clean (lowercase ASCII, punycode for international names).
Silently "fixing" a value would hide a problem in the upstream data and break the
link between our output and the original row, so a bad value is reported instead.
"""

import csv
import functools
import string
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow.parquet as pq
import tldextract

COLUMN = "root_domain"

# RFC 1035 limits, in characters, for an ASCII hostname without a trailing dot.
MAX_LABEL_LENGTH = 63
MAX_HOST_LENGTH = 253

LABEL_CHARS = frozenset(string.ascii_lowercase + string.digits + "-")
PUNYCODE_PREFIX = "xn--"


@dataclass(frozen=True)
class Domain:
    row: int  # 1-based position in the input file, so a report points to the exact row
    original_input: str  # exactly as in the file
    display_name: str  # punycode decoded, for human-readable reports only

    @property
    def host(self) -> str:
        """The ASCII name used for network and DNS.

        Equal to original_input because a row is accepted only if it is already a
        valid ASCII hostname; this property names that intent at the call site.
        """
        return self.original_input


@dataclass(frozen=True)
class InvalidRow:
    row: int
    value: str | None  # None when the file has a null in that row
    reason: str


@dataclass
class LoadResult:
    domains: list[Domain] = field(default_factory=list)
    invalid: list[InvalidRow] = field(default_factory=list)


def _decode_punycode_label(label: str) -> str:
    """Decode one `xn--` label, raising UnicodeError if it is not valid punycode.

    Python's "idna" codec (IDNA 2003) is used rather than the plain "punycode" codec:
    the plain codec accepts garbage such as `xn--a` (decoding it to a control character),
    while the idna codec rejects invalid characters and labels that do not re-encode
    to the same text.
    """
    return label.encode("ascii").decode("idna")


def _label_problem(label: str) -> str | None:
    if label == "":
        return "empty label (leading, trailing or double dot)"
    if len(label) > MAX_LABEL_LENGTH:
        return f"label longer than {MAX_LABEL_LENGTH} characters: {label!r}"
    bad = sorted(set(label) - LABEL_CHARS)
    if bad:
        return f"label {label!r} has characters outside a-z 0-9 -: {''.join(bad)!r}"
    if label.startswith("-") or label.endswith("-"):
        return f"label starts or ends with a hyphen: {label!r}"
    if label.startswith(PUNYCODE_PREFIX):
        try:
            _decode_punycode_label(label)
        except UnicodeError:
            return f"label is not valid punycode: {label!r}"
    return None


def validate_hostname(value: str | None) -> str | None:
    """Return why `value` is not a valid lowercase ASCII hostname, or None if it is.

    Only the first problem found is returned: one clear reason per row is enough
    for a human to fix the source data.
    """
    if value is None or value == "":
        return "empty value"
    if not value.isascii():
        return "non-ASCII characters (international names must be in punycode)"
    if value != value.lower():
        return "uppercase letters"
    if len(value) > MAX_HOST_LENGTH:
        return f"longer than {MAX_HOST_LENGTH} characters"
    labels = value.split(".")
    # A single label (e.g. "localhost") is a hostname but not a website domain.
    if len(labels) < 2:
        return "needs at least two labels (e.g. example.com)"
    for label in labels:
        problem = _label_problem(label)
        if problem:
            return problem
    return None


def to_display_name(host: str) -> str:
    """Decode punycode labels for humans: xn--jobvrkstedet-9cb.nu -> jobværkstedet.nu.

    Call only on a validated host; an invalid punycode label would raise here.
    """
    return ".".join(
        _decode_punycode_label(label) if label.startswith(PUNYCODE_PREFIX) else label
        for label in host.split(".")
    )


def _read_values(path: Path) -> list[str | None]:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        table = pq.read_table(path)
        if COLUMN not in table.column_names:
            raise ValueError(f"{path}: no {COLUMN!r} column (found {table.column_names})")
        return table.column(COLUMN).to_pylist()
    if suffix in (".csv", ".txt"):
        # newline="" keeps the text exactly as stored; csv/splitlines handle \r\n.
        with path.open(encoding="utf-8", newline="") as f:
            if suffix == ".csv":
                values = [row[0] if row else "" for row in csv.reader(f)]
            else:
                values = f.read().splitlines()
        if suffix == ".csv" and values and values[0] == COLUMN:
            values = values[1:]  # header row, not data
        return values
    raise ValueError(f"{path}: unsupported file type {suffix!r} (use .parquet, .csv or .txt)")


def load_domains(path: Path) -> LoadResult:
    """Read the domain list. Valid rows become Domains; every other row is reported."""
    result = LoadResult()
    for row, value in enumerate(_read_values(path), start=1):
        reason = validate_hostname(value)
        if reason:
            result.invalid.append(InvalidRow(row=row, value=value, reason=reason))
        else:
            assert value is not None  # validate_hostname rejects None
            result.domains.append(
                Domain(row=row, original_input=value, display_name=to_display_name(value))
            )
    return result


@functools.cache
def _offline_extractor() -> tldextract.TLDExtract:
    # suffix_list_urls=() means "never download the Public Suffix List"; cache_dir=None
    # means "no cached copy". Results then depend only on the installed tldextract.
    return tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


def registered_domain(host: str) -> str:
    """The part of a host name a company registers: static.example.co.uk -> example.co.uk.

    Uses the public (ICANN) suffixes of tldextract's bundled list, never the network.
    A host without a known suffix (e.g. "localhost") is returned unchanged.
    """
    host = host.lower().rstrip(".")
    return _offline_extractor()(host).top_domain_under_public_suffix or host


def domain_name(host: str) -> str:
    """The registered name without its public suffix.

    www.velvety-cosmetics.com -> velvety-cosmetics (offline, like registered_domain).
    """
    host = host.lower().rstrip(".")
    return _offline_extractor()(host).domain or host
