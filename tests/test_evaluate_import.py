"""Importing the blind inspection notes into the ground truth."""

from techdetect.evaluate.importer import import_blind, parse_inspection_file, resolve_names
from techdetect.evaluate.labels import empty_ground_truth

NAMES = [
    "WordPress",
    "jQuery",
    "Google Font API",
    "Microsoft 365",
    "Amazon Route 53",
    "World4You",
    "Hostgator",
    "Polylang",
    "Wix",
]

NOTES = """# Manual inspection

## site-one.com
- Checked: 2026-11-20, Chrome incognito, no extensions, before consent
- Status: works

| Technology | Where I saw it (tab + exact value) |
|---|---|
| WordPress | Elements: generator `WordPress 7.1.4` |
| jQuery 3.7.1 | Console: `jQuery.fn.jquery` = `3.7.1` |
| Google Fonts | Network: `fonts.googleapis.com` |
| Matomo (self-hosted) | Network: `matomo.js` |

WordPress plugins / themes / Shopify apps:
- Polylang (plugin): Headers `x-redirect-by: Polylang`
- Button Block 1.2.9 (plugin): Network `/wp-content/plugins/button-block/`

After consent only:
- not checked

DNS only:
- MX: `x.mail.protection.outlook.com` -> Microsoft 365
- TXT / SPF: `v=spf1 include:spf.w4ymail.at -all` -> World4You mail
- NS: `ns1.hostgator.com` -> HostGator / websitewelcome DNS; `google-site-verification=abc`

Notes (anything odd):
- WooCommerce: NOT counted, CSS only.

Raw notes (as first written):
```
- jQuery, React, everything
```

## site-two.com
- Checked: 2026-11-20, headless Chromium + curl + Resolve-DnsName, before consent
- Status: works

| Technology | Where I saw it (tab + exact value) |
|---|---|
| Wix | Page source: generator `Wix.com Website Builder` |

WordPress plugins / themes / Shopify apps:
- none (Wix). Wix apps seen: Wix FAQ (`faq-ooi`); Wix Forms (`forms-viewer`)

DNS only:
- NS: `ns-1.awsdns-01.org` -> Amazon Route 53
"""


def parsed():
    return parse_inspection_file(NOTES)


def test_table_plugins_and_dns_are_read_and_raw_notes_ignored() -> None:
    one = parsed()["site-one.com"]
    typed = [(e["typed"], e["section"]) for e in one.entries]
    assert typed == [
        ("WordPress", "website"),
        ("jQuery 3.7.1", "website"),
        ("Google Fonts", "website"),
        ("Matomo", "website"),
        ("Polylang", "plugin"),
        ("Button Block 1.2.9", "plugin"),
        ("Microsoft 365", "dns"),
        ("World4You", "dns"),  # the service word "mail" is dropped for DNS lines
        ("HostGator", "dns"),  # "HostGator / websitewelcome DNS; token": one host
    ]
    assert one.status == "works"
    assert "React" not in [e["typed"] for e in one.entries]  # raw notes are not labels


def test_names_matched_to_rules_versions_kept_unknown_names_flagged() -> None:
    records = {r["technology"]: r for r in resolve_names(parsed()["site-one.com"], NAMES)}

    assert records["jQuery"]["version"] == "3.7.1"
    assert records["Microsoft 365"]["in_rules"]  # the number is part of the name
    assert records["Google Font API"]["typed"] == "Google Fonts"  # a checked alias
    assert records["Hostgator"]["in_rules"]  # names compare case-insensitively
    assert records["Button Block"] == {
        **records["Button Block"],
        "in_rules": False,
        "version": "1.2.9",
        "section": "plugin",
    }
    # Its alias names a rule this small list does not have: kept as written, flagged.
    assert not records["Matomo"]["in_rules"]


def test_who_found_it_comes_from_the_checked_line() -> None:
    one = resolve_names(parsed()["site-one.com"], NAMES)
    two = resolve_names(parsed()["site-two.com"], NAMES)
    assert {r["found_by"] for r in one} == {"me"}
    assert {r["found_by"] for r in two} == {"blind_session"}


def test_apps_listed_after_none_are_still_imported() -> None:
    two = {r["technology"] for r in resolve_names(parsed()["site-two.com"], NAMES)}
    assert {"Wix", "Wix FAQ", "Wix Forms", "Amazon Route 53"} <= two


def test_import_fills_blind_domains_and_keeps_existing_lists() -> None:
    data = empty_ground_truth()
    data["sets"]["blind"] = ["site-one.com", "site-two.com", "missing.com"]
    data["domains"]["site-two.com"] = {
        "human_found": [{"technology": "Wix"}],
        "verdicts": {},
        "missed": [],
    }

    outcome = import_blind(data, parsed(), NAMES, force=False)

    assert outcome["site-one.com"] == "imported 9 technologies"
    assert outcome["site-two.com"].startswith("kept")
    assert outcome["missing.com"] == "not in the inspection file"
    assert data["domains"]["site-one.com"]["site_status_label"] == "works"

    import_blind(data, parsed(), NAMES, force=True)
    assert len(data["domains"]["site-two.com"]["human_found"]) == 4
