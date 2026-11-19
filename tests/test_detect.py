"""`detect`: domain status, counting groups, outputs (F3.6), with the real pinned rules."""

import csv
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from raw_sites import Site
from typer.testing import CliRunner

from techdetect.cli import app
from techdetect.collect.run import RUN_COMPLETE
from techdetect.detect import build_summary, counting_group, domain_status
from techdetect.fingerprints.combine import PatternGroup, TechnologyResult
from techdetect.fingerprints.loader import RuleSet, load_rules

REAL_RULES = Path(__file__).parents[1] / "fingerprints"
runner = CliRunner()


@pytest.fixture(scope="module")
def rules() -> RuleSet:
    return load_rules(REAL_RULES)


# ------------------------------------------------------------ domain status (F3.6)


def page(status: str, category: str | None = None) -> dict:
    return {"status": status, "error_category": category}


@pytest.mark.parametrize(
    ("static", "browser", "expected"),
    [
        (page("ok"), page("failed", "bot_challenge"), "ok"),  # one real page is enough
        (page("failed", "http_4xx"), page("ok"), "ok"),
        (page("failed", "bot_challenge"), page("failed", "http_4xx"), "bot_challenge"),
        (page("failed", "conn_refused"), page("failed", "http_5xx"), "failed:http_error"),
        (page("failed", "dns_fail"), page("failed", "dns_fail"), "failed:dns_error"),
        (page("failed", "timeout"), None, "failed:timeout"),
        (page("failed", "rate_limited"), page("failed", "conn_error"), "failed:rate_limited"),
        (None, None, "failed:not_collected"),
    ],
)
def test_domain_status(static: dict | None, browser: dict | None, expected: str) -> None:
    assert domain_status(static, browser) == expected


# ------------------------------------------------------------ counting groups (D3)


def result(name: str, tier: str = "observed", signals: tuple[str, ...] = ("html",)):
    groups = tuple(
        PatternGroup(s, None, None, "", 100, tier, 1, ("static.html",), (), ()) for s in signals
    )
    return TechnologyResult(name, 100, (), tier, () if tier == "inferred" else groups)


def test_counting_groups(rules: RuleSet) -> None:
    assert counting_group(result("WordPress"), rules) == "core"
    assert counting_group(result("PHP", tier="inferred"), rules) == "inferred_only"
    assert counting_group(result("Google Workspace", signals=("dns",)), rules) == "dns_only"
    # DNS plus website evidence stays core.
    assert counting_group(result("Cloudflare", signals=("dns", "headers")), rules) == "core"
    # Themes are reported apart; a theme that is also a page builder stays core.
    assert counting_group(result("Astra"), rules) == "themes"
    assert counting_group(result("Divi"), rules) == "core"
    # Plugins count like any technology: Jetpack is only a WordPress plugin.
    assert counting_group(result("Jetpack"), rules) == "core"
    assert counting_group(result("WooCommerce"), rules) == "core"


def test_summary_counts_names_once_and_respects_the_threshold(rules: RuleSet) -> None:
    def line(domain: str, *techs: tuple[str, int]) -> dict:
        return {
            "domain": domain,
            "status": "ok",
            "redirected_to": None,
            "technologies": [
                {"name": n, "confidence": c, "group": "core", "categories": []} for n, c in techs
            ],
        }

    lines = [line("a.com", ("WordPress", 100), ("Lodash", 0)), line("b.com", ("WordPress", 100))]

    everything = build_summary(lines, rules, threshold=0, partial=False)
    assert everything["unique_technologies"]["core"] == 2  # WordPress counts once
    assert everything["core_per_domain"]["average"] == 1.5

    confident = build_summary(lines, rules, threshold=50, partial=False)
    assert confident["core_technologies"] == ["WordPress"]


# ------------------------------------------------------------ the command, end to end


def make_run(tmp_path: Path) -> tuple[Path, Path]:
    """A finished collection of six domains, and the input list naming them."""
    raw = tmp_path / "raw"
    # A working WordPress site, twice (to check name-level counting).
    Site(raw, "blog-one.com").static("wordpress.html")
    Site(raw, "blog-two.com").static("wordpress.html")
    # A dead domain: both fetches failed on DNS, no record answered.
    Site(raw, "gone-away.com").static("plain.html", status="failed", error_category="dns_fail")
    # A domain whose page ends on another registered domain.
    Site(raw, "old-shop.com").static("wordpress.html", final_url="https://www.new-shop.com/")
    # A bot wall, but DNS still tells something.
    walled = Site(raw, "walled-site.com").static(
        "plain.html", status="failed", error_category="bot_challenge"
    )
    walled.dns(("walled-site.com", "MX", ["1 aspmx.l.google.com."]))
    (raw / RUN_COMPLETE).write_text("{}", encoding="utf-8")
    # "never-collected.com" is in the input but has no folder.
    hosts = [
        "blog-one.com",
        "blog-two.com",
        "gone-away.com",
        "old-shop.com",
        "walled-site.com",
        "never-collected.com",
    ]
    domains = tmp_path / "domains.txt"
    domains.write_text("\n".join(hosts) + "\n", encoding="utf-8")
    return raw, domains


def run_detect(raw: Path, domains: Path, out: Path, *extra: str):
    args = [
        "detect",
        "--input",
        str(domains),
        "--raw-dir",
        str(raw),
        "--output-dir",
        str(out),
        "--rules-dir",
        str(REAL_RULES),
        "--workers",
        "1",
        *extra,
    ]
    return runner.invoke(app, args)


def test_detect_writes_one_line_per_input_domain(tmp_path: Path) -> None:
    raw, domains = make_run(tmp_path)
    out = tmp_path / "out"
    result = run_detect(raw, domains, out)
    assert result.exit_code == 0, result.output

    lines = [json.loads(x) for x in (out / "detections.jsonl").read_text("utf-8").splitlines()]
    by_domain = {line["domain"]: line for line in lines}
    assert len(lines) == 6  # = input domains, failed ones included, in input order
    assert [line["domain"] for line in lines][0] == "blog-one.com"

    # A dead domain: failed:dns_error and an empty detections list.
    assert by_domain["gone-away.com"]["status"] == "failed:dns_error"
    assert by_domain["gone-away.com"]["technologies"] == []
    assert by_domain["never-collected.com"]["status"] == "failed:not_collected"

    # a.com -> b.com: redirected_to is the other registered domain.
    assert by_domain["old-shop.com"]["redirected_to"] == "new-shop.com"
    assert by_domain["blog-one.com"]["redirected_to"] is None

    # A bot wall: page not matched, DNS evidence still reported (in its own group).
    walled = by_domain["walled-site.com"]
    assert walled["status"] == "bot_challenge"
    assert [(t["name"], t["group"]) for t in walled["technologies"]] == [
        ("Google Workspace", "dns_only")
    ]

    # Every detection carries its proof.
    wordpress = next(
        t for t in by_domain["blog-one.com"]["technologies"] if t["name"] == "WordPress"
    )
    assert wordpress["versions"] == ["6.6.2"]
    example = wordpress["evidence"][0]["examples"][0]
    assert {"source_artifact", "location", "matched_value", "context"} <= set(example)


def test_summary_separates_groups_and_failures(tmp_path: Path) -> None:
    raw, domains = make_run(tmp_path)
    out = tmp_path / "out"
    run_detect(raw, domains, out)
    summary = json.loads((out / "summary.json").read_text("utf-8"))

    assert summary["partial_run"] is False
    assert summary["domains"]["failed"] == {"dns_error": 1, "not_collected": 1}
    assert summary["domains"]["redirected_elsewhere"]["list"] == ["old-shop.com -> new-shop.com"]
    # Averages over ok domains that were not redirected, with the denominator written out.
    assert summary["core_per_domain"]["denominator"] == "over 2 ok domains of 6"
    assert summary["core_technologies"].count("WordPress") == 1  # name level
    assert summary["unique_technologies"]["separately"]["dns_only"] == 1  # Google Workspace
    assert summary["unique_technologies"]["separately"]["inferred_only"] >= 1  # MySQL


def test_tables_have_one_row_per_domain_and_technology(tmp_path: Path) -> None:
    raw, domains = make_run(tmp_path)
    out = tmp_path / "out"
    run_detect(raw, domains, out)

    with (out / "technologies.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert set(rows[0]) == {
        "domain",
        "technology",
        "categories",
        "version",
        "confidence",
        "tier",
        "group",
        "signals",
    }
    wordpress = next(
        r for r in rows if r["domain"] == "blog-one.com" and r["technology"] == "WordPress"
    )
    assert wordpress["version"] == "6.6.2"
    assert pq.read_table(out / "technologies.parquet").num_rows == len(rows)


def test_detect_refuses_an_unfinished_collection(tmp_path: Path) -> None:
    raw, domains = make_run(tmp_path)
    (raw / RUN_COMPLETE).unlink()

    refused = run_detect(raw, domains, tmp_path / "out")
    assert refused.exit_code == 1
    assert "--allow-partial" in refused.output

    allowed = run_detect(raw, domains, tmp_path / "out", "--allow-partial")
    assert allowed.exit_code == 0, allowed.output
    summary = json.loads((tmp_path / "out" / "summary.json").read_text("utf-8"))
    assert summary["partial_run"] is True
