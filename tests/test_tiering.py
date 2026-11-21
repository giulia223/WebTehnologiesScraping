"""Task 7.5: a browser only where it pays, decided from the cheap tier's result."""

from typing import Any

from techdetect.evaluate.tiering import browser_reason, tiered_lines


def tech(name: str, *categories: str, group: str = "core") -> dict[str, Any]:
    return {"name": name, "categories": list(categories), "group": group}


def line(domain: str, *techs: dict, page: str = "used") -> dict[str, Any]:
    return {"domain": domain, "technologies": list(techs), "artifacts": {"static.html": page}}


def test_the_three_reasons_to_send_the_browser() -> None:
    assert browser_reason(line("shop.com", tech("WooCommerce", "Ecommerce"))) == "shop"
    gtm = line("tags.com", tech("Google Tag Manager", "Tag managers"))
    assert browser_reason(gtm) == "tag manager"
    blocked = line("blocked.com", page="not used: bot_challenge")
    assert browser_reason(blocked) == "plain fetch blocked (bot_challenge)"


def test_a_site_that_is_down_is_not_sent() -> None:
    for why in ("dns_fail", "http_5xx", "timeout", "http_4xx"):
        assert browser_reason(line("down.com", page=f"not used: {why}")) is None


def test_a_plain_site_stays_in_the_cheap_tier() -> None:
    assert browser_reason(line("plain.com", tech("WordPress", "CMS"))) is None
    # Only the site's own (core) technologies count, not a DNS record.
    assert browser_reason(line("mail.com", tech("Shopify", "Ecommerce", group="dns_only"))) is None


def test_tiered_lines_use_the_browser_result_only_where_the_policy_says() -> None:
    cheap = [line("shop.com", tech("Shopify", "Ecommerce")), line("plain.com")]
    full = [
        line("shop.com", tech("Shopify", "Ecommerce"), tech("Hotjar", "Analytics")),
        line("plain.com", tech("React", "JavaScript frameworks")),
    ]
    lines, sent = tiered_lines(cheap, full)
    assert sent == {"shop.com": "shop"}
    assert lines[0] is full[0]  # rendered: browser result
    assert lines[1] is cheap[1]  # not rendered: React stays unseen, the price of the tier
