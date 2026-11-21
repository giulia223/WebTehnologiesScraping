"""Signal ablation (Task 7.1): detection re-run with fewer collection steps."""

import json
from pathlib import Path

from raw_sites import Site

from techdetect.detect import detect_all
from techdetect.domains import Domain
from techdetect.evaluate.ablation import ROWS, check_against_summary, step_costs

REAL_RULES = Path(__file__).parents[1] / "fingerprints"
STATIC_ROW, STATIC_PLUS_ROW, BROWSER_ROW, FULL_ROW = (sources for _, sources, _ in ROWS)


def names(line: dict) -> dict[str, str]:
    return {t["name"]: t["group"] for t in line["technologies"]}


def next_js_site(raw: Path) -> Domain:
    # Next.js is visible only as a JS variable in the browser; it implies React and Webpack.
    Site(raw, "next-site.com").static("plain.html").browser(
        "plain.html", js={"next.version": "14.2.3"}
    )
    return Domain(1, "next-site.com", "next-site.com")


def test_a_browser_only_technology_appears_only_once_the_browser_is_allowed(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw"
    domain = next_js_site(raw)
    [static] = detect_all(raw, [domain], REAL_RULES, sources=STATIC_ROW)
    [browser] = detect_all(raw, [domain], REAL_RULES, sources=BROWSER_ROW)
    assert "Next.js" not in names(static)
    assert names(browser)["Next.js"] == "core"
    # The browser's files are listed as left out, not as missing.
    assert static["artifacts"]["rendered.html"] == "not used: left out"
    # Status comes from both fetches in every row, so every row counts the same domains.
    assert static["status"] == browser["status"] == "ok"


def test_an_implied_technology_disappears_with_its_implier(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    domain = next_js_site(raw)
    [static] = detect_all(raw, [domain], REAL_RULES, sources=STATIC_ROW)
    [browser] = detect_all(raw, [domain], REAL_RULES, sources=BROWSER_ROW)
    assert names(browser)["React"] == "inferred_only"
    assert names(browser)["Webpack"] == "inferred_only"
    # Re-run, not filtered: without Next.js nothing implies React or Webpack.
    assert "React" not in names(static) and "Webpack" not in names(static)


def test_the_full_row_is_normal_detection(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    domain = next_js_site(raw)
    Site(raw, "mail-site.com").static("plain.html").dns(
        ("mail-site.com", "MX", ["1 aspmx.l.google.com."])
    )
    domains = [domain, Domain(2, "mail-site.com", "mail-site.com")]
    assert detect_all(raw, domains, REAL_RULES, sources=FULL_ROW) == detect_all(
        raw, domains, REAL_RULES
    )
    [_, without_dns] = detect_all(raw, domains, REAL_RULES, sources=BROWSER_ROW)
    assert "Google Workspace" not in names(without_dns)


def test_step_costs_use_each_steps_latest_run(tmp_path: Path) -> None:
    log = tmp_path / "_run_log.jsonl"
    records = [
        ("2026-11-17T00:00:00Z", "static", 9000),  # older run of static: ignored
        ("2026-11-20T00:00:00Z", "static", 2000),
        ("2026-11-20T00:00:00Z", "static", 4000),
        ("2026-11-17T00:00:00Z", "dns", 1000),  # dns was not re-run: its only run counts
    ]
    log.write_text(
        "\n".join(
            json.dumps({"run_started_at": r, "step": s, "duration_ms": ms, "domain": "x"})
            for r, s, ms in records
        ),
        encoding="utf-8",
    )
    assert step_costs(log) == {"static": 3.0, "dns": 1.0}


def test_the_check_against_summary_json() -> None:
    result = {"rows": [{"unique_core": 332, "unique_extended": 396}]}
    same = {"unique_technologies": {"core": 332, "extended_total": 396}}
    other = {"unique_technologies": {"core": 341, "extended_total": 405}}
    assert check_against_summary(result, same) is None
    assert "differs from summary.json" in (check_against_summary(result, other) or "")
