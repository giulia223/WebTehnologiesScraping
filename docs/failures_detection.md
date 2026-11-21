## Detection failures: wrong and missed detections (2026-11-20)

The groups above are about reading the sites. This part is about what the tool concluded from the pages it did read, on the 20 labelled domains (`docs/METRICS.md`: every false positive with its evidence, every miss). Judged against `docs/DEFINITIONS.md`. Run 2b's detections.

### False positives: 35 detections judged wrong, by cause

| Cause | Wrong | Examples | Possible fix |
|---|---|---|---|
| A hosted platform's infrastructure credited to the site | 16 | Wix site: Cloudflare, Varnish, Amazon S3, Let's Encrypt, Wix's own Sentry. Weebly site: Cloudflare, Amazon S3, Weebly's Snowplow tracker. IONOS site: Apache, Sectigo | **Done (run 3):** on a hosted builder's site, these move to a separate group, `platform_infra`, reported but not counted as the site's |
| A rule matches a similar but different product | 8 | Lodash on `_.VERSION` 1.8.3 / 1.13.8, which is Underscore (and Lodash's rule then removes the real Underscore); Vimeo from a Weebly class named `VimeoPlayer`; "WebsiteBuilder" from Wix's generator text; Store Vantage from Wix's `svSession` cookie; MyWebsite Creator from the IONOS editor's host; Apple iCloud Mail from Apple's general domain check; PWA from a manifest link alone | Correct or switch off the faulty pattern |
| A mention taken as use | 5 | Amazon S3 and Klarna listed in a security-policy header; a PayPal logo image; "DPD" inside a CSS class name; "wixstores" inside Wix's page data | Do not let header lists, image names or class names prove use |
| Google Tag Manager reported for `gtag.js` | 3 | 3 sites load `googletagmanager.com/gtag/js?id=G-...` (Google Analytics), with no `GTM-` container | Require a `GTM-` container or `gtm.js` |
| A wrong inference | 3 | Cloudflare inferred from cdnjs (2 sites): cdnjs runs on Cloudflare, the site does not; Duda inferred from the wrong MyWebsite Creator | Drop the cdnjs -> Cloudflare inference; fixing MyWebsite Creator fixes Duda |

All 35 come from rules, from what is credited to whom, or from our matcher; none from collection. Checked against Wappalyzer's own behaviour, 2 were our matcher's fault, now fixed (run 3b): "DPD" matched a CSS class because `text` rules read `<style>` contents, and a confidence-0 Lodash hint removed a sure Underscore. 33 of the 35 have confidence 100, so a confidence threshold cannot remove them.

### Misses: 39 technologies the blind inspection found and the tool does not report

**No rule exists (26):** no matcher fix can find them. Wix apps (FAQ, Forms, Bookings, Members), WordPress theme names (Bridge, Bridge Child), small jQuery plugins (carouFredSel, Toast), Google Consent Mode, Jetpack Stats, LayerSlider, a local hosting company (Netsons, Just Host), a certificate authority (Google Trust Services), and site-specific services (Psychology Today's verified seal, Drouot, Skills for Life). These are coverage gaps: new rules, or the discovery step.

**A rule exists (13), by cause:**

| Cause | Misses | Detail | Possible fix |
|---|---|---|---|
| The rule covers another form of the same evidence | 7 | Animate.css: the file is `animate-<hash>.css` (an asset pipeline renamed it), the rule wants `animate.min.css`. BootstrapCDN: a stylesheet from `maxcdn.bootstrapcdn.com`, the rule only knows the script. IONOS: name servers `1and1-dns`, the rule only knows `ui-dns`. Hostgator: name servers `hostgator.com`, the rule only reads the SOA record. PHP: a form posting to `login.php`, the rule only knows two file names. Jetpack: its stats variables (`_stq`, `wpcom`), the rule only knows its plugin folder. Google Ads: a `gtag.js` tag with an `AW-` id, the rule has no pattern for it | Reviewed custom rules adding the missing form (custom rules add to a rule, they never replace it) |
| The evidence was saved, but not where the rule looks | 2 | Google Font API: the font stylesheet is requested by a CSS `@import`, so there is no `<link>` tag. Google Maps: its script loads inside the page's map frame, not in the page's own `<script>` tags. Both requests are in `browser.json` | Also test script and stylesheet rules against the browser's network requests (a matcher change; would differ from Wappalyzer's own behaviour) |
| The inspection looked at the redirect target | 2 | seringa-safaris.com redirects to seringasafaris.com; the inspection's Microsoft 365 and GoDaddy come from the target's DNS. The tool reads the input domain's DNS only | Also read the DNS of a same-company redirect target |
| The inspection saw another page | 1 | Cloudflare Turnstile appeared only on the challenge page shown to the inspection's headless browser; the tool got the real page | None needed |
| The label is doubtful under the definitions | 1 | Magnific Popup: only its stylesheet was seen; the script (the product) was not | Review the label |

One DNS query also timed out (seringa-safaris.com's SOA record), which alone would have hidden Hostgator: a retry of timed-out DNS queries would help.

### Fixable vs inherent

- **Reading the sites:** of the 43 domains not read as a working site, almost all are inherent: dead, broken, parked or a placeholder, or behind a bot wall. Only the 2 timeouts might work on another day.
- **Detection:** all 35 false positives are fixable in rules or in what is credited. Of the 39 misses, 26 need new rules, 9 need rule or matcher changes, 2 need the target's DNS, and 2 are not tool failures.

### What was done about them (2026-11-20)

Each false positive was first checked against Wappalyzer's own behaviour (our copy of every pattern is exact):
- **Our matcher's fault (2), fixed** (run 3b): "DPD" in a CSS class (text rules now read visible text only); a confidence-0 Lodash hint removing a sure Underscore.
- **Wappalyzer's looser reading, now followed (6 labels changed to correct)**: Google Tag Manager from `gtag.js`, PWA from a manifest, payment providers the site lists or allows (Klarna, PayPal). See `docs/DEFINITIONS.md`, "How strict".
- **A hosted platform's infrastructure (16), reported apart** (run 3, group `platform_infra`).
- **Too broad even for Wappalyzer, or another company's infrastructure (9 on the labelled domains, 30 on all 200), corrected** (run 3c, `fingerprints/corrections/`): WebsiteBuilder, Store Vantage, Vimeo, MyWebsite Creator and the Duda inferred from it, cdnjs -> Cloudflare, Amazon S3 from security-policy headers, Apple iCloud Mail.
- **Wix eCommerce on a Bookings site, corrected** (run 3d): replaced by a reviewed rule requiring the installed "Wix Stores" app.
- **Still wrong (4)**: Lodash where both libraries are only at confidence 0; AWS inferred from the hosting company Acquia, PHP and MySQL inferred from Weebly (inferences through a supplier or a hosted builder are not yet moved to `platform_infra`).
- **Saved but not where the rule looks, partly fixed** (run 3e): Google Fonts loaded through CSS is now found from the page's own stylesheet requests; Google Maps inside a frame stays a miss on purpose (frames and script requests add mostly wrong detections).
