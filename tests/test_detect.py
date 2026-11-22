"""`detect`: domain status, counting groups, outputs, with the real pinned rules."""

import csv
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from raw_sites import Site
from typer.testing import CliRunner

from techdetect.cli import app
from techdetect.collect.run import RUN_COMPLETE
from techdetect.detect import (
    DEFAULT_MIN_CONFIDENCE,
    _rows,
    build_summary,
    counting_group,
    domain_status,
    hosted_platform,
    move_platform_infrastructure,
)
from techdetect.fingerprints.combine import PatternGroup, TechnologyResult
from techdetect.fingerprints.loader import RuleSet, load_rules

REAL_RULES = Path(__file__).parents[1] / "fingerprints"
runner = CliRunner()


@pytest.fixture(scope="module")
def rules() -> RuleSet:
    return load_rules(REAL_RULES)


# ------------------------------------------------------------ domain status


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


def test_counting_groups() -> None:
    assert counting_group(result("WordPress")) == "core"
    # Implied technologies count in core; the tier still says "inferred".
    assert counting_group(result("PHP", tier="inferred")) == "core"
    assert counting_group(result("Google Workspace", signals=("dns",))) == "dns_only"
    # DNS plus website evidence stays core.
    assert counting_group(result("Cloudflare", signals=("dns", "headers"))) == "core"
    # Themes, plugins and apps count like any technology used to build the site.
    assert counting_group(result("Astra")) == "core"
    assert counting_group(result("Jetpack")) == "core"


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


def test_a_detection_whose_clues_are_all_worth_0_is_not_counted_by_default(
    rules: RuleSet,
) -> None:
    # Livefyre's `L.version` pattern is worth 0; on the real sites the `L` was Leaflet's.
    lines = [
        {
            "domain": "maps.com",
            "status": "ok",
            "redirected_to": None,
            "technologies": [
                {
                    "name": name,
                    "confidence": confidence,
                    "group": "core",
                    "categories": [],
                    "versions": [],
                    "tier": "observed",
                    "signals": ["js"],
                }
                for name, confidence in (("Leaflet", 100), ("Livefyre", 0))
            ],
        }
    ]
    assert DEFAULT_MIN_CONFIDENCE == 1
    summary = build_summary(lines, rules, DEFAULT_MIN_CONFIDENCE, partial=False)
    assert summary["core_technologies"] == ["Leaflet"]
    assert summary["confidence_threshold"] == 1
    assert [r["technology"] for r in _rows(lines, DEFAULT_MIN_CONFIDENCE)] == ["Leaflet"]


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
    assert summary["confidence_threshold"] == DEFAULT_MIN_CONFIDENCE  # the command's default
    assert summary["domains"]["failed"] == {"dns_error": 1, "not_collected": 1}
    assert summary["domains"]["redirected_elsewhere"]["list"] == ["old-shop.com -> new-shop.com"]
    # Averages over ok domains that were not redirected, with the denominator written out.
    assert summary["core_per_domain"]["denominator"] == "over 2 ok domains of 6"
    assert summary["core_technologies"].count("WordPress") == 1  # name level
    assert summary["unique_technologies"]["separately"]["dns_only"] == 1  # Google Workspace
    assert "MySQL" in summary["core_technologies"]  # implied by WordPress: core
    assert set(summary["unique_technologies"]["separately"]) == {"dns_only", "platform_infra"}


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


def test_tables_hold_only_the_counted_domains(tmp_path: Path) -> None:
    # old-shop.com redirects to another company's site: it stays in detections.jsonl
    # with its status, but not in the tables, so their unique core names equal the headline.
    raw, domains = make_run(tmp_path)
    out = tmp_path / "out"
    run_detect(raw, domains, out)
    with (out / "technologies.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert "old-shop.com" not in {r["domain"] for r in rows}
    lines = [json.loads(t) for t in (out / "detections.jsonl").read_text("utf-8").splitlines()]
    assert next(x for x in lines if x["domain"] == "old-shop.com")["technologies"]
    summary = json.loads((out / "summary.json").read_text("utf-8"))
    core = {r["technology"] for r in rows if r["group"] == "core"}
    assert len(core) == summary["unique_technologies"]["core"]


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


# ------------------------------------------------------------ not a website, block pages


@pytest.mark.parametrize(
    "title",
    [
        "Default page",
        "Site Hospedado na KingHost | Data Center no Brasil",
        "Hello! Welcome to Synology Web Station!",
        "Coming Soon",
        "無効なURLです",
    ],
)
def test_placeholder_titles_from_the_real_examples(title: str) -> None:
    ok = {"status": "ok", "final_url": "https://x.com/"}
    assert domain_status(ok, ok, (title, title)) == "placeholder"


@pytest.mark.parametrize(
    "title", ["Coming soon: our winter collection | Shop", "Default Pages Ltd - Printing", "Home"]
)
def test_real_titles_are_not_placeholders(title: str) -> None:
    ok = {"status": "ok", "final_url": "https://x.com/"}
    assert domain_status(ok, ok, (title, title)) == "ok"


@pytest.mark.parametrize(
    "final_url",
    [
        "https://cmc01village.com/lander",
        "https://p2-1.afp24.net/lander_v4.php?id=abc",
        "https://cf.trim-rise.site/middle.html?impId=pXWunZ",
    ],
)
def test_parking_landers_from_the_real_examples(final_url: str) -> None:
    landed = {"status": "ok", "final_url": final_url}
    assert domain_status(None, landed) == "parked"


def test_saved_block_pages_are_reread_with_todays_rules(rules: RuleSet, tmp_path: Path) -> None:
    from techdetect.detect import detect_domain
    from techdetect.domains import Domain
    from techdetect.fingerprints.matcher import RuleIndex

    akamai = tmp_path / "akamai.html"
    akamai.write_text(
        "<html><head><title>Access Denied</title></head><body>You don't have permission to "
        "access this on this server.<p>Reference&#32;&#35;18&#46;36496768&#46;179</p></body>"
        "</html>",
        encoding="utf-8",
    )
    cdn = tmp_path / "cdn.html"
    cdn.write_text(
        "<html><head><title>DNS resolution error | gone-site.com | Cloudflare</title></head>"
        "</html>",
        encoding="utf-8",
    )
    raw = tmp_path / "raw"
    # Both were saved by the collector as plain HTTP errors.
    Site(raw, "blocked-site.com").static(
        akamai, status="failed", error_category="http_4xx", http_status=403
    )
    Site(raw, "gone-site.com").static(
        cdn, status="failed", error_category="http_4xx", http_status=409
    )
    index = RuleIndex(rules)

    blocked = detect_domain(raw, Domain(1, "blocked-site.com", "blocked-site.com"), rules, index)
    gone = detect_domain(raw, Domain(2, "gone-site.com", "gone-site.com"), rules, index)
    assert blocked["status"] == "bot_challenge"
    assert gone["status"] == "failed:dns_error"


def test_parked_and_placeholder_domains_are_left_out_of_the_counts(rules: RuleSet) -> None:
    def line(domain: str, status: str) -> dict:
        tech = {"name": "Nginx", "confidence": 100, "group": "core", "categories": []}
        return {"domain": domain, "status": status, "redirected_to": None, "technologies": [tech]}

    lines = [line("real.com", "ok"), line("lander.com", "parked"), line("soon.com", "placeholder")]
    summary = build_summary(lines, rules, threshold=0, partial=False)

    assert summary["domains"]["parked"]["list"] == ["lander.com"]
    assert summary["domains"]["placeholder"]["list"] == ["soon.com"]
    assert summary["core_per_domain"]["denominator"] == "over 1 ok domains of 3"


def test_domain_for_sale_page_is_parked(tmp_path: Path) -> None:
    from techdetect.detect import says_for_sale

    raw = tmp_path / "raw"
    sale = tmp_path / "sale.html"
    sale.write_text(
        "<html><head><title>parrots-example.de steht zum Verkauf</title></head><body>"
        "This domain is for sale. Place your bid.</body></html>",
        encoding="utf-8",
    )
    Site(raw, "parrots-example.de").static(sale)
    page = {"status": "ok", "html_file": "static.html"}
    folder = raw / "parrots-example.de"

    assert says_for_sale(folder, page, "parrots-example.de steht zum Verkauf", "parrots-example.de")
    assert says_for_sale(folder, page, "", "parrots-example.de")  # the body says it too
    ok = {"status": "ok", "final_url": "https://parrots-example.de/"}
    assert domain_status(ok, ok, for_sale=True) == "parked"


def test_a_shop_selling_things_is_not_a_domain_for_sale(tmp_path: Path) -> None:
    from techdetect.detect import says_for_sale

    raw = tmp_path / "raw"
    shop = tmp_path / "shop.html"
    shop.write_text(
        "<html><head><title>Used cars for sale | Example Motors</title></head><body>"
        "Cars for sale. Haus zu verkaufen. Buy this car today.</body></html>",
        encoding="utf-8",
    )
    Site(raw, "example-motors.com").static(shop)
    page = {"status": "ok", "html_file": "static.html"}
    title = "Used cars for sale | Example Motors"
    assert not says_for_sale(raw / "example-motors.com", page, title, "example-motors.com")


@pytest.mark.parametrize(
    ("host", "target", "same"),
    [
        ("seringa-safaris.com", "seringasafaris.com", True),  # only a hyphen differs
        ("velvety.eu", "velvety-cosmetics.com", True),  # one name inside the other
        ("fsbwyoming.com", "myfrbank.com", False),  # a renamed bank
        ("maiquotes.net", "mymarylandauto.com", False),
        ("allegrocreditbeta.com", "synchrony.com", False),  # the parent company
        ("abc.com", "abcdpinturas.com.br", False),  # too short to prove anything
    ],
)
def test_same_name_redirects_from_the_real_examples(host: str, target: str, same: bool) -> None:
    from techdetect.detect import same_name

    assert same_name(host, target) is same


def test_same_name_redirect_is_counted_for_the_input(rules: RuleSet) -> None:
    def line(domain: str, target: str | None, same: bool) -> dict:
        tech = {"name": "Nginx", "confidence": 100, "group": "core", "categories": []}
        return {
            "domain": domain,
            "status": "ok",
            "redirected_to": target,
            "redirect_same_name": same,
            "technologies": [tech],
        }

    lines = [
        line("seringa-safaris.com", "seringasafaris.com", True),
        line("allegrocreditbeta.com", "synchrony.com", False),
    ]
    summary = build_summary(lines, rules, threshold=0, partial=False)

    assert summary["domains"]["redirected_same_name"]["list"] == [
        "seringa-safaris.com -> seringasafaris.com"
    ]
    assert summary["domains"]["redirected_elsewhere"]["list"] == [
        "allegrocreditbeta.com -> synchrony.com"
    ]
    assert summary["core_per_domain"]["denominator"] == "over 1 ok domains of 2"


# ------------------------------------------------- a hosted platform's infrastructure


def site(*techs: tuple[str, list[str]]) -> list[dict]:
    return [{"name": n, "categories": c, "group": "core", "confidence": 100} for n, c in techs]


def moved(techs: list[dict]) -> list[str]:
    return [t["name"] for t in techs if t["group"] == "platform_infra"]


def test_wix_infrastructure_moves_page_code_and_features_stay() -> None:
    # appelpsychotherapy.com, as detected; the 7 moved are the ones labelled "not the site's".
    techs = site(
        ("Wix", ["CMS", "Blogs"]),
        ("Cloudflare", ["CDN"]),
        ("Cloudflare Bot Management", ["Security"]),
        ("Varnish", ["Caching"]),
        ("Amazon S3", ["CDN"]),
        ("Amazon Web Services", ["PaaS"]),
        ("Let's Encrypt", ["SSL/TLS certificate authorities"]),
        ("Sentry", ["Issue trackers"]),
        ("React", ["JavaScript frameworks"]),
        ("Google Analytics", ["Analytics"]),
        ("HSTS", ["Security"]),
    )
    techs.append({"name": "Google Workspace", "categories": ["Email"], "group": "dns_only"})

    assert move_platform_infrastructure(techs) == "Wix"
    assert moved(techs) == [
        "Cloudflare",
        "Cloudflare Bot Management",
        "Varnish",
        "Amazon S3",
        "Amazon Web Services",
        "Let's Encrypt",
        "Sentry",
    ]
    assert techs[1]["platform"] == "Wix"
    # The domain's own mail (DNS) and things the page itself carries are untouched.
    assert {t["name"]: t["group"] for t in techs[-4:]} == {
        "React": "core",
        "Google Analytics": "core",
        "HSTS": "core",
        "Google Workspace": "dns_only",
    }


def test_weebly_and_ionos_examples() -> None:
    weebly = site(
        ("Weebly", ["CMS"]),
        ("Google Hosted Libraries", ["CDN"]),
        ("Snowplow Analytics", ["Analytics", "IaaS"]),
        ("jQuery", ["JavaScript libraries"]),
    )
    ionos = site(
        ("MyWebsite", ["Page builders"]),
        ("Apache HTTP Server", ["Web servers"]),
        ("Sectigo", ["SSL/TLS certificate authorities"]),
        ("Modernizr", ["JavaScript libraries"]),
    )
    move_platform_infrastructure(weebly)
    move_platform_infrastructure(ionos)
    assert moved(weebly) == ["Google Hosted Libraries", "Snowplow Analytics"]
    assert moved(ionos) == ["Apache HTTP Server", "Sectigo"]


def test_platforms_added_by_the_name_check() -> None:
    # Real sites from the name check, as detected.
    dish = site(  # hansis-knusperhendl-foodtruck.eatbu.com
        ("DISH", ["Reservations & delivery"]),
        ("Google Cloud CDN", ["CDN"]),
        ("Adobe Analytics", ["Analytics"]),
        ("Adobe Client Data Layer", ["Customer data platform"]),
        ("Adobe Experience Platform Identity Service", ["Customer data platform"]),
        ("Adobe Experience Platform Launch", ["Tag managers"]),
        ("Swiper", ["JavaScript libraries"]),
    )
    bandzoogle = site(  # davidloeppke.com
        ("Bandzoogle", ["CMS"]),
        ("OpenResty", ["Web servers"]),
        ("Varnish", ["Caching"]),
        ("Ruby on Rails", ["Web frameworks"]),
        ("Turbo", ["JavaScript frameworks"]),
    )
    square = site(  # abbikadabbisbakingco.com
        ("Square Online", ["Ecommerce"]),
        ("Datadog", ["RUM"]),
        ("Vue.js", ["JavaScript frameworks"]),
    )
    wix = site(("Wix", ["CMS"]), ("Firebase", ["Databases"]), ("React", ["JavaScript frameworks"]))
    for techs in (dish, bandzoogle, square, wix):
        move_platform_infrastructure(techs)
    assert moved(dish) == [
        "Google Cloud CDN",
        "Adobe Analytics",
        "Adobe Client Data Layer",
        "Adobe Experience Platform Identity Service",
        "Adobe Experience Platform Launch",
    ]
    # Page code bundled by the builder (Turbo, Vue.js, React, Swiper) still counts.
    assert moved(bandzoogle) == ["OpenResty", "Varnish", "Ruby on Rails"]
    assert moved(square) == ["Datadog"]
    assert moved(wix) == ["Firebase"]


def test_a_platforms_own_tool_stays_on_other_sites() -> None:
    # Datadog is the platform's own only on Square Online; a site's own Datadog counts.
    techs = site(("WordPress", ["CMS"]), ("Datadog", ["RUM"]))
    assert move_platform_infrastructure(techs) is None
    assert moved(techs) == []


def test_a_self_hosted_site_keeps_its_infrastructure() -> None:
    # footasylum.com chose Cloudflare and Akamai itself: nothing moves.
    techs = site(("Next.js", ["JavaScript frameworks"]), ("Cloudflare", ["CDN"]))
    assert move_platform_infrastructure(techs) is None
    assert moved(techs) == []


def test_an_inferred_platform_does_not_move_anything() -> None:
    # Duda only implied by another detection is not proof the site runs on Duda.
    techs = site(("Cloudflare", ["CDN"]))
    techs.append(implied("Duda", "MyWebsite Creator", ["CMS"]))
    assert move_platform_infrastructure(techs) is None


def implied(name: str, by: str, categories: list[str]) -> dict:
    return {
        "name": name,
        "categories": categories,
        "group": "core",
        "confidence": 100,
        "tier": "inferred",
        "implied_by": by,
    }


def test_what_a_hosted_platform_implies_is_the_platforms_except_page_code() -> None:
    # 5rcircleprocess.weebly.com: Weebly implies PHP and MySQL (its own server side).
    weebly = site(("Weebly", ["CMS"]))
    weebly += [implied("PHP", "Weebly", ["Programming languages"])]
    weebly += [implied("MySQL", "Weebly", ["Databases"])]
    # A Wix site: Wix implies React, which runs on the site's page.
    wix = site(("Wix", ["CMS"])) + [implied("React", "Wix", ["JavaScript frameworks"])]
    move_platform_infrastructure(weebly)
    move_platform_infrastructure(wix)
    assert moved(weebly) == ["PHP", "MySQL"]
    assert moved(wix) == []


def test_an_implied_technology_follows_what_implies_it() -> None:
    # A Webflow site: Amazon S3 is the platform's, so AWS implied by it is too (a chain).
    webflow = site(("Webflow", ["Page builders"]), ("Amazon S3", ["CDN"]))
    webflow += [implied("Amazon Web Services", "Amazon S3", ["PaaS"])]
    webflow += [implied("Example Child", "Amazon Web Services", ["Miscellaneous"])]
    move_platform_infrastructure(webflow)
    assert moved(webflow) == ["Amazon S3", "Amazon Web Services", "Example Child"]
    # On a self-hosted site, WordPress implies PHP: core. From a DNS-only record: DNS-only.
    own = site(("WordPress", ["CMS"])) + [implied("PHP", "WordPress", ["Programming languages"])]
    own.append({"name": "Mail Host", "categories": ["Email"], "group": "dns_only"})
    own.append(implied("Mail Suite", "Mail Host", ["Email"]))
    move_platform_infrastructure(own)
    assert {t["name"]: t["group"] for t in own} == {
        "WordPress": "core",
        "PHP": "core",
        "Mail Host": "dns_only",
        "Mail Suite": "dns_only",
    }


def test_a_platform_below_the_threshold_is_not_the_sites_platform() -> None:
    # haus-muehlenfeld-pommerby.de: Duda only at confidence 0, MyWebsite at 100.
    duda = {"name": "Duda", "categories": ["CMS"], "group": "core", "confidence": 0}
    mywebsite = {"name": "MyWebsite", "categories": ["CMS"], "group": "core", "confidence": 100}
    assert hosted_platform([duda, mywebsite]) == "MyWebsite"
    assert hosted_platform([duda]) is None


def test_platform_infrastructure_is_not_counted_as_the_sites(rules: RuleSet) -> None:
    def line(domain: str, group: str) -> dict:
        tech = {"name": "Varnish", "confidence": 100, "group": group, "categories": []}
        return {"domain": domain, "status": "ok", "redirected_to": None, "technologies": [tech]}

    only_platform = build_summary([line("wix-site.com", "platform_infra")], rules, 0, False)
    assert only_platform["unique_technologies"]["core"] == 0
    assert only_platform["unique_technologies"]["extended_total"] == 0
    assert only_platform["unique_technologies"]["separately"]["platform_infra"] == 1

    both = build_summary(
        [line("wix-site.com", "platform_infra"), line("own-server.com", "core")], rules, 0, False
    )
    assert both["unique_technologies"]["core"] == 1  # chosen by one site: it counts there
