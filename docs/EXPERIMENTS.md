# Experiments

Every change that affects the numbers gets a row in the runs table below, naming the hypothesis it tests.

## Hypotheses

These claims come from research and from other projects. They are **not verified**. Each one is confirmed or refuted by a run below, and the outcome is reported with numbers.

"webappanalyzer" is the open technology-fingerprint database (a maintained continuation of Wappalyzer's last open rule set) that this tool matches against.

| ID | Claim (unverified) | Tested by | Result |
|---|---|---|---|
| H1 | A browser finds ~30-40% more technologies than static HTML | Comparing detections from the static fetch vs the rendered browser page | - |
| H2 | webappanalyzer rules alone give ~300-430 unique technologies on this list | The first full detection run | Run 1: 347 core, 414 extended: in range. Precision not measured yet. |
| H3 | The browser tier costs ~8x the static tier per domain | Timing the static and browser collectors, then the throughput benchmark | - |
| H4 | Subpages mainly add payment / chat / forms / reviews technologies | Crawling a few subpages per site and comparing | - |
| H5 | DNS reveals SaaS (email, CRM, support) invisible in HTML | Adding DNS records as a signal | - |
| H6 | Each coverage expansion lowers precision unless its evidence tier is accounted for | Measuring precision after each expansion | - |
| H7 | Analytics/marketing tools on Shopify stores are often visible only in network traffic (web pixels), not in HTML | Comparing HTML vs network-request evidence on Shopify stores | - |
| H8 | An honest robot User-Agent gets noticeably more bot-check pages / fewer technologies than a Chrome User-Agent | Re-running the static fetch with a Chrome User-Agent | - |

## Rule set

Every run below states the rule-set version it used (printed by `techdetect rules`). Changing it is a change that affects the numbers and gets its own row.

| Rule-set version | Upstream commit (date) | Technologies | Patterns | Since |
|---|---|---|---|---|
| `webappanalyzer@9e52f10a9533+custom:none` | `9e52f10a9533c8f7f8d0703c843f7295d56c95a3` (2026-11-15) | 7,704 | 15,838 (0 skipped) | 2026-11-17 |

## Runs

Column meanings:
- **Unique core**: distinct technology names (no versions) with at least one *observed* (seen working) or *declared* (the site states it) piece of evidence from the website itself.
- **Unique extended**: core plus the groups reported separately: technologies reached only by inference (e.g. WordPress implies PHP), WordPress plugins/themes, Shopify apps, and technologies found only through DNS.

| Run | Date | What changed | Hypothesis | Unique core | Unique extended | Detections | Precision | Recall | Runtime | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| 0: collection | 2026-11-17 | First full collection of all 200 domains, all five steps (code `0.1.0+1e0a67458837`, rules `webappanalyzer@9e52f10a9533+custom:none`, crawl from RO) | H3 | - | - | - | - | - | 747 s (12.5 min) | No detection yet. Average time per domain: static 3.63 s, DNS 2.14 s, TLS 0.65 s, robots.txt 0.88 s, browser 9.47 s; browser / static = 2.6x per domain (H3 claims ~8x; the throughput benchmark is still to come). Wall time per step with concurrency 10 (HTTP steps) and 4 (browser): static 170 s, DNS 45 s, TLS 25 s, robots.txt 25 s, browser 476 s. A real page: static 152/200, browser 159/200, either 163/200 (4 only static, 11 only browser). Browser final URL differs from static on 6 domains. Failures, static: http_4xx 22, bot_challenge 12, dns_fail 7, http_5xx 3, timeout 2, tls_error 1, conn_error 1; browser: http_4xx 20, dns_fail 7, bot_challenge 7, http_5xx 3, timeout 2, conn_error 2. No crashes, no missing files. |
| 1: detection | 2026-11-19 | First full detection on Run 0's data: all webappanalyzer rules, homepage only, static + browser + DNS + TLS + robots.txt (code `0.1.0+8a2a49cc8ec1` plus the uncommitted detect step; rules `webappanalyzer@9e52f10a9533+custom:none`; confidence threshold 0) | H2 | 347 | 414 | 2,701 | - | - | 15 s (19 processes) | Extended = core + 41 DNS-only + 10 only inferred + 16 themes. Plugins and apps count in core (52 WordPress plugins detected, 0 Shopify apps); themes are reported apart. 7 domains redirected to another registered domain are left out of all counts. Domain status: 163 ok, 5 bot_challenge, 23 http_error, 7 dns_error, 2 timeout. 14.8 core technologies per domain, over 156 ok domains (not redirected) of 200; none with 0. Core at confidence >= 50: 346, >= 80: 345. For reference: with plugins and apps also reported apart, core would be 329 (only pure plugins/themes apart) or 293 (anything with a plugin, theme or app category apart). Veridion reported 477 for the same list, method not stated. |
| 1b: domain statuses | 2026-11-20 | Same data and rules as Run 1; detect only: new statuses `parked` and `placeholder` (left out of all counts), saved block pages re-read (Akamai deny page by its text, Cloudflare block page), Cloudflare error 1001 = dead site | - | 347 | 414 | - | - | - | 16 s | Statuses: 155 ok (was 163), 10 bot_challenge (5), 10 dns_error (7), 15 http_error (23), 2 timeout, 3 parked, 5 placeholder. Core per domain 15.3 over 150 ok domains of 200 (was 14.8 over 156). Unique counts unchanged: the technologies on parked and placeholder pages also appear on real sites. A manual check found a collector bug on 6 domains (certificate for another name: the host's default page taken instead of the real `http://` site), not fixed in this run. |
| 2: re-collection | 2026-11-20 | Static, TLS, robots.txt and browser steps re-collected (DNS kept from Run 0) with the fetch fix: a page reached only by ignoring a certificate made for another name no longer ends the search, and `http://www.` is tried last (code `0.1.0+299d64387388`); detect adds a "domain for sale" page rule | - | 342 | 408 | - | - | - | 13 min collect, 16 s detect | The 6 domains collected through a certificate for another name now give their real site: dioxoxigenio.com.br (16 core technologies, was the KingHost default page), wrinkle-pei.cz (19, was the Synology default page), den-a-sha.co.jp, amaronbattery.in, cmresolutions.com, maiquotes.net. Statuses: 157 ok, 10 bot_challenge, 11 dns_error (ferienwohnung-kastell.de stopped resolving since Run 0), 15 http_error, 2 timeout, 3 parked, 2 placeholder, 5 redirected. All 45 non-blind domains checked by hand now have the status a person saw. Core per domain 15.2 over 152 ok domains. Core 347 -> 342 and extended 414 -> 408 also include 3 days of site changes; the earlier output was not kept, so the change cannot be split by name. Average s/domain: static 4.08, browser 10.12. |
| 2b: same-name redirects | 2026-11-20 | Detect only, Run 2's data: a redirect to the same company's other domain (same name once the ending, hyphens and dots are removed, or one name inside the other) is counted for the input domain | - | 344 | 410 | - | - | - | 25 s | Credited: velvety.eu -> velvety-cosmetics.com, seringa-safaris.com -> seringasafaris.com. Still left out: fsbwyoming.com -> myfrbank.com, maiquotes.net -> mymarylandauto.com, allegrocreditbeta.com -> synchrony.com. Core per domain 15.3 over 154 ok domains of 200. |
