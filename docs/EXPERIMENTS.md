# Experiments

Every change that affects the numbers gets a row in the runs table below, naming the hypothesis it tests.

## Hypotheses

These claims come from research and from other projects. They are **not verified**. Each one is confirmed or refuted by a run below, and the outcome is reported with numbers.

"webappanalyzer" is the open technology-fingerprint database (a maintained continuation of Wappalyzer's last open rule set) that this tool matches against.

| ID | Claim (unverified) | Tested by | Result |
|---|---|---|---|
| H1 | A browser finds ~30-40% more technologies than static HTML | Comparing detections from the static fetch vs the rendered browser page | - |
| H2 | webappanalyzer rules alone give ~300-430 unique technologies on this list | The first full detection run | - |
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
