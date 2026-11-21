"""Lead list (Task 8.1): one row of facts per domain, no score."""

import csv
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from techdetect.cli import app
from techdetect.report.leads import COLUMNS, build_leads, only_seen_by_browser, write_leads


def tech(name: str, *artifacts: str, group: str = "core") -> dict[str, Any]:
    return {
        "name": name,
        "group": group,
        "evidence": [
            {
                "signal": "scriptSrc",
                "key": None,
                "artifacts": list(artifacts),
                "examples": [
                    {
                        "source_artifact": artifacts[0],
                        "matched_value": "cdn.shopify.com",
                        "location": "https://cdn.shopify.com/s/files/x.js",
                    }
                ],
            }
        ],
    }


def line(domain: str, *techs: dict, status: str = "ok", **extra: Any) -> dict[str, Any]:
    return {
        "domain": domain,
        "status": status,
        "redirected_to": extra.get("redirected_to"),
        "redirect_same_name": extra.get("redirect_same_name", False),
        "technologies": list(techs),
    }


LINES = [
    line("moved.com", tech("Nginx", "static.json"), redirected_to="other.com"),
    line(
        "store.com",
        tech("Shopify", "static.html", "rendered.html"),
        tech("Facebook Pixel", "browser.json"),  # only the browser saw it
        tech("Google Workspace", "dns.json", group="dns_only"),
        tech("PHP", "static.json", group="inferred_only"),  # not listed: only implied
    ),
    line("dead.com", status="failed:dns_error"),
    line("parked.com", tech("Sedo", "static.html"), status="parked", redirected_to="lander.net"),
]


def by_domain(rows: list[dict]) -> dict[str, dict]:
    return {r["domain"]: r for r in rows}


def test_a_shopify_store_with_its_proof_and_facts() -> None:
    row = by_domain(build_leads(LINES))["store.com"]
    assert row["shopify"] == "yes"
    assert row["shopify_evidence"].startswith("scriptSrc: 'cdn.shopify.com' in static.html")
    assert row["technologies"] == "Facebook Pixel; Shopify"  # core only, sorted
    assert row["technology_count"] == 2
    assert row["only_seen_by_browser"] == "Facebook Pixel"
    assert row["dns_only"] == "Google Workspace"
    assert row["note"] == ""


def test_seen_only_by_the_browser_needs_every_piece_of_evidence_from_it() -> None:
    assert only_seen_by_browser(tech("X", "browser.json", "rendered.html"))
    assert not only_seen_by_browser(tech("X", "rendered.html", "static.html"))


def test_one_row_per_domain_and_another_companys_site_last_without_technologies() -> None:
    rows = build_leads(LINES)
    assert [r["domain"] for r in rows] == ["store.com", "dead.com", "parked.com", "moved.com"]
    moved = rows[-1]
    assert moved["note"].startswith("redirects to other.com, another company's site")
    assert moved["technologies"] == "" and moved["technology_count"] == 0


def test_failed_and_parked_domains_say_why() -> None:
    rows = by_domain(build_leads(LINES))
    assert rows["dead.com"]["note"] == "site not reachable (dns_error)"
    # Parked wins over its redirect to a parking lander, and its detections are not listed.
    assert rows["parked.com"]["note"] == "parked or for-sale page, no website"
    assert rows["parked.com"]["technologies"] == ""


def test_a_same_company_redirect_is_counted() -> None:
    same = line(
        "brand.eu",
        tech("WordPress", "static.html"),
        redirected_to="brand.com",
        redirect_same_name=True,
    )
    [row] = build_leads([same])
    assert row["technologies"] == "WordPress"
    assert row["note"] == "redirects to brand.com, the same company: counted for this domain"


def test_the_report_command_writes_leads_csv(tmp_path: Path) -> None:
    import json

    (tmp_path / "detections.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in LINES), encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["report", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Shopify stores: 1 (store.com)" in result.output
    with (tmp_path / "leads.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == list(COLUMNS)
    assert len(rows) == len(LINES)


def test_write_leads_returns_the_path(tmp_path: Path) -> None:
    assert write_leads(build_leads(LINES), tmp_path) == tmp_path / "leads.csv"
