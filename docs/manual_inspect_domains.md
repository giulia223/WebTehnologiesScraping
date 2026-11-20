# Manual inspection of the 10 blind domains (Task 0.3)

Blind set: every 20th input row (20, 40, ..., 200), picked by position. Each domain is inspected
by hand, independently of the tool, before looking at the tool's output for it.

## clotting-guide.online
- Checked: 2026-11-20, Chrome incognito, no extensions, before consent
- Final URL: https://www.clotting-guide.online/de/ueber/    Redirected: yes, 2 redirects:
  `https://clotting-guide.online/` -> 302 `https://www.clotting-guide.online/` -> 302 (x-redirect-by: Polylang) `/de/ueber/`
- Status: works
- Cookie banner: yes

| Technology | Where I saw it (tab + exact value) |
|---|---|
| WordPress | Elements: `<meta name="generator" content="WordPress 7.1.4" />`; robots.txt `Disallow: /wp-admin/`, `Sitemap: .../wp-sitemap.xml` |
| Apache HTTP Server | Headers (every response): `server: Apache` |
| jQuery 3.7.1 | Console: `jQuery.fn.jquery` = `3.7.1`, globals `$`, `jQuery`; script `c0.wp.com/c/7.1.5/wp-includes/js/jquery/jquery.min.js` |
| jQuery Migrate 3.4.1 | Console: `jQuery.migrateVersion` = `3.4.1`, `jQuery.isWindow()` deprecation warning; script `.../jquery/jquery-migrate.min.js` |
| React 18.3.1 | Elements: `<script id="react-js"`; Console: `React.version` = `18.3.1`, globals `React`, `ReactDOM`; script `c0.wp.com/c/7.1.5/wp-includes/js/dist/vendor/react.min.js` |
| Jetpack | Console globals `_stq`, `wpcom`, `st_go`, `linktracker_init` |
| Jetpack Stats (WordPress.com Stats) | Network: script `stats.wp.com/e-202647.js`, image `pixel.wp.com` |
| Jetpack Site Accelerator (WordPress.com CDN) | Network: WordPress core files served from `c0.wp.com/c/7.1.5/...` |
| jQuery carouFredSel 6.2.1 | Elements: `/wp-content/themes/vogue/js/jquery.carouFredSel-6.2.1-packed.js?ver=10.9.3` |
| js-cookie 2.2.1 | Elements: `/wp-content/plugins/wpfront-notification-bar/jquery-plugins/js-cookie.min.js?ver=2.2.1`; Console global `Cookies` |
| Twemoji | Console globals `_wpemojiSettings`, `twemoji`; script `/wp-includes/js/wp-emoji-release.min.js?ver=7.1.5` |
| Google Fonts | Network: stylesheet `fonts.googleapis.com/css?family=Lato...Open+Sans`, fonts from `fonts.gstatic.com` |
| Font Awesome 6.5.1 | Network: `/wp-content/themes/vogue/includes/font-awesome/css/all.min.css?ver=6.5.1` |
| Let's Encrypt | Certificate: Issued by O = Let's Encrypt, CN = YR1 |

WordPress plugins / themes / Shopify apps:
- Polylang (plugin): Headers `x-redirect-by: Polylang`; Cookies `pll_language`; Headers `vary: Accept-Language`
- WPFront Notification Bar 3.5.2 (plugin): Elements `/wp-content/plugins/wpfront-notification-bar/...?ver=3.5.2.09291`; Cookies `wpfront-notification-bar-keep-closed`; Console `wpfront_notification_bar`
- Button Block 1.2.9 (plugin): Network `/wp-content/plugins/button-block/build/view.css?ver=1.2.9`, `.../build/view.js`
- Vogue 10.9.3 (theme): Elements `/wp-content/themes/vogue/...?ver=10.9.3`; body class `wp-theme-vogue`

After consent only:
- not checked

DNS only:
- MX: `mail.clotting-guide.online` (preference 10): mail on the site's own domain
- TXT / SPF: `v=spf1 mx include:spf.w4ymail.at -all` -> World4You mail
- NS: `ns1.world4you.at`, `ns2.world4you.at` -> World4You (Austrian hosting / DNS)
- DMARC: `_dmarc` TXT `v=DMARC1;p=none;`

Notes (anything odd):
- WooCommerce: NOT counted. The page has 63 mentions of `.woocommerce` / `add_to_cart_button`, but all are inline CSS from the Vogue theme (body class `vogue-shop-remove-titlebar`); no WooCommerce script, no `wc_add_to_cart_params` / `woocommerce_params`, no WooCommerce cookie, no `woocommerce` class on `<body>`.
- WordPress version: 7.1.4 at the first look, 7.1.5 on 2026-11-20 (generator tag and `c0.wp.com/c/7.1.5/`): the site was updated in between.
- Language: the root redirects to the German page `/de/ueber/` both for an English (`en-US`) and a German browser; `vary: Accept-Language` on the Polylang redirect.
- React here is WordPress's own bundled copy (`wp-includes/js/dist/vendor/react.min.js`), loaded for a block on the page; it still runs on the page.
- Headers: `link: <https://www.clotting-guide.online/wp-json/>; rel="https://api.w.org/"` (WordPress REST API) on the final page.
- No analytics found: `dataLayer`, `gtag`, `ga`, `fbq` all undefined.
- No PHP header (`x-powered-by` absent): PHP is not seen directly, only implied by WordPress.

Raw notes (as first written):
```
clotting-guide.online
- 2 redirects: https://www.clotting-guide.online/ ->  /de/ueber/
- server: Apache
- plugin:  x-redirect-by: Polylang (WorPress)
- from network: jQuery, React, jetpack, ..
- <meta name="generator" content="WordPress 7.1.4" />
- "https://www.clotting-guide.online/wp-content/themes/vogue/js/jquery.carouFredSel-6.2.1-packed.js?ver=10.9.3"></script>
- <script id="react-js"
-    .woocommerce ul.products li.product a.add_to_cart_button,
- <script id="js-cookie-js" src="https://www.clotting-guide.online/wp-content/plugins/wpfront-notification-bar/jquery-plugins/js-cookie.min.js?ver=2.2.1"></script>
- "jQuery.isWindow() ['$', 'jQuery', 'Cookies', 'wpfront_notification_bar', '__load_wpfront_notification_bar', 'trapFocus', 'trapSearch', '_stq', 'React', 'ReactDOM', 'wp', 'st_go', 'linktracker_init', 'wpcom', '_wpemojiSettings', 'twemoji']
- cookies wpfront-notification-bar-keep-closed, pll_language
- User-agent: *
Disallow: /wp-admin/
Allow: /wp-admin/admin-ajax.php

Sitemap: https://www.clotting-guide.online/wp-sitemap.xml
- connection YR1
Organizația (O)	Let's Encrypt
```

## rionecontrastanga.net
- Checked: 2026-11-20, headless Chromium + curl + Resolve-DnsName, before consent
- Final URL: https://rionecontrastanga.net/        Redirected: no
- Status: works
- Cookie banner: no

| Technology | Where I saw it (tab + exact value) |
|---|---|
| WordPress 6.5.15 | Page source: `<meta name="generator" content="WordPress 6.5.15" />`; Headers: `link: <https://rionecontrastanga.net/wp-json/>; rel="https://api.w.org/"`; Network: `/wp-includes/...?ver=6.5.15` |
| PHP 7.4.34 | Headers: `x-powered-by: PHP/7.4.34` |
| jQuery 3.7.1 | Console: `jQuery.fn.jquery` = `3.7.1`; Network: `/wp-includes/js/jquery/jquery.min.js?ver=3.7.1` |
| jQuery Migrate 3.4.1 | Console: `jQuery.migrateVersion` = `3.4.1`, log `JQMIGRATE: Migrate is installed, version 3.4.1` |
| jQuery UI 1.13.2 | Network: `/wp-includes/js/jquery/ui/core.min.js?ver=1.13.2` (+ accordion, datepicker, dialog, tabs, effects...) |
| WPBakery Page Builder 6.8.0 | Page source: `<meta name="generator" content="Powered by WPBakery Page Builder ...">`; Network: `/wp-content/plugins/js_composer/assets/js/dist/js_composer_front.min.js?ver=6.8.0`; Console globals `vc_js`, `wpb_prepare_tab_content` |
| Slider Revolution 6.8.1 | Page source: `<meta name="generator" content="Powered by Slider Revolution 6.8.1 ...">`; Network: `/wp-content/plugins/revslider/public/js/sr7.js?ver=6.8.1`; Console globals `SR7`, `revapi1` |
| LayerSlider 6.7.5 | Page source: `<meta name="generator" content="Powered by LayerSlider 6.7.5 ...">`; Network: `/wp-content/plugins/LayerSlider/static/layerslider/js/layerslider.kreaturamedia.jquery.js?ver=6.7.5`; Console globals `_layerSlider`, `LS_Meta` |
| GSAP (GreenSock) | Network: `/wp-content/plugins/LayerSlider/static/layerslider/js/greensock.js?ver=1.19.0`, `/wp-content/themes/bridge/js/plugins/TweenLite.min.js`; Console globals `TweenMax`, `TimelineMax`, `GreenSockGlobals` |
| Contact Form 7 5.9.8 | Network: `/wp-content/plugins/contact-form-7/includes/js/index.js?ver=5.9.8`, fetch `/wp-json/contact-form-7/v1/contact-forms/194/feedback/schema`; Console global `wpcf7` |
| Isotope | Network: `/wp-content/plugins/js_composer/assets/lib/bower/isotope/dist/isotope.pkgd.min.js?ver=6.8.0`; Console global `Isotope` |
| imagesLoaded | Network: `/wp-content/themes/bridge/js/plugins/imagesloaded.js`; Console global `imagesLoaded` |
| jQuery carouFredSel 6.2.1 | Network: `/wp-content/themes/bridge/js/plugins/jquery.carouFredSel-6.2.1.min.js?ver=6.5.15` |
| Modernizr | Console globals `Modernizr`, `yepnope` |
| MediaElement.js | Console globals `mejs`, `MediaElement`, `MediaElementPlayer` |
| Font Awesome 4.7.0 | Network: `/wp-content/themes/bridge/css/font-awesome/css/font-awesome.min.css`, font `fontawesome-webfont.woff2?v=4.7.0` |
| Google Fonts | Network: stylesheet `fonts.googleapis.com/css?family=Raleway...Alfa+Slab+One...Roboto`, fonts from `fonts.gstatic.com` |
| Twemoji | Network: `/wp-includes/js/wp-emoji-release.min.js?ver=6.5.15`; Console globals `_wpemojiSettings`, `twemoji` |
| Let's Encrypt | Certificate: Issued by O = Let's Encrypt, CN = YR1 |

WordPress plugins / themes / Shopify apps:
- Bridge (theme, by Qode Interactive): Network `/wp-content/themes/bridge/...`; Console globals `qode`, `qodeGlobalVars`, `QodeAjaxUrl`
- Bridge Child (child theme): Network `/wp-content/themes/bridge-child/style.css`
- WPBakery Page Builder 6.8.0 (plugin `js_composer`): see table
- Slider Revolution 6.8.1 (plugin `revslider`): see table
- LayerSlider 6.7.5 (plugin): see table
- Contact Form 7 5.9.8 (plugin): see table
- Smash Balloon Instagram Feed 6.16.0 (plugin `instagram-feed`): Network `/wp-content/plugins/instagram-feed/js/sbi-scripts.min.js?ver=6.16.0`; Console globals `sbi`, `sb_instagram_js_options`
- Joinchat 6.4.4 (plugin `creame-whatsapp-me`, WhatsApp chat button): Network `/wp-content/plugins/creame-whatsapp-me/public/js/joinchat.min.js?ver=6.4.4`; Console global `joinchat_obj`; WhatsApp button visible bottom-left in the screenshot

After consent only:
- not checked

DNS only:
- MX: `mail.rionecontrastanga.net` (preference 0): mail on the site's own domain
- TXT / SPF: `v=spf1 +a +mx ~all` (no third-party sender)
- NS: `dns1..dns4.netsons.net` -> Netsons (Italian hosting / DNS)
- DMARC: none (`_dmarc` has no TXT record)

Notes (anything odd):
- Web server: NOT counted. No `server` header on the page or on robots.txt. The `alt-svc: h3=":443"; ma=2592000, h3-29=":443"` header only says HTTP/3 is offered; it does not name the server.
- Theme version: unknown. Every Bridge file has `?ver=6.5.15`, which is the WordPress version (WordPress adds its own version when a theme gives none), so it is not the theme's version.
- One image is hot-linked from the theme vendor's demo site: `bridge170.qodeinteractive.com/wp-content/uploads/2018/02/home-background-img-2.jpg` (left over from the Bridge demo import). Not a separate technology.
- Instagram Feed plugin loads its script, but no request to Instagram was seen before consent.
- Facebook / Instagram icons in the header are plain links, not trackers. No analytics: `dataLayer`, `gtag`, `ga`, `fbq` all undefined; no cookies at all.
- Other theme-bundled libraries seen only as globals (not counted separately): `skrollr`, `Chart`, `Typed`, `NiceScroll`, `Masonry`, `Packery`, `fluidvids`, `BigText`, `pp_alreadyInitialized` (prettyPhoto). Icon fonts loaded from the theme: Elegant Icons, Linea Icons, Dripicons.
- robots.txt exists (`#Simple Robots.txt 0.1`, only Crawl-delay for MSNBot / bingbot): nothing WordPress-specific in it.
- Certificate subject is `autodiscover.rionecontrastanga.net`, not the bare domain (the certificate also covers it, the page loads without warnings).

## loizillon.com
- Checked: 2026-11-20, headless Chromium + curl + Resolve-DnsName, before consent
- Final URL: https://www.loizillon.com/        Redirected: yes, `https://loizillon.com/` -> 301 `https://www.loizillon.com/`
- Status: works (the site of the auction house "Acteon - Compiegne Encheres", in French)
- Cookie banner: yes (own banner "NOUS RESPECTONS VOTRE VIE PRIVEE", buttons "Personnaliser mes choix" / "Tout accepter")

| Technology | Where I saw it (tab + exact value) |
|---|---|
| Google Analytics (GA4, via gtag.js) | Network: script `www.googletagmanager.com/gtag/js?id=G-HCJ5MXKWJ2`; Page source: `gtag('config', 'G-HCJ5MXKWJ2');`; Console: `typeof gtag` = `function`, globals `dataLayer`, `google_tag_data` |
| Google Consent Mode | Page source: `gtag('consent', 'default', {...})`; Console: `dataLayer[0]` = consent default with `ad_storage`, `analytics_storage` ... = `denied` |
| Java | Cookies: `JSESSIONID` (domain `www.loizillon.com`, set by the page's `set-cookie` header) |
| jQuery 1.12.4 | Console: `jQuery.fn.jquery` = `1.12.4`; Network: `cdn.drouot.com/assets?name=cpg/jquery-743584baf8fc09dcd82ba03b2c8d990e.js` |
| Bootstrap | Network: stylesheet `/assets/bootstrap-2702cc3efe0973add8272e0853ab03a3.css` (CSS only, version not visible) |
| Font Awesome 6.5.0 | Network: `cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css`, font `.../6.5.0/webfonts/fa-solid-900.woff2`; also a local copy `/assets/font-awesome/css/font-awesome.min-....css` |
| cdnjs | Network: `cdnjs.cloudflare.com` (Font Awesome stylesheet and font) |
| jsDelivr | Network: `cdn.jsdelivr.net/npm/vanilla-lazyload@17.1.2/dist/lazyload.min.js` |
| vanilla-lazyload 17.1.2 | Network: see jsDelivr row; Console global `LazyLoad` |
| Owl Carousel | Network: `/assets/include/carousel-plugin/owl.carousel.min-58f126a356f7a1eb5ef4de741979f957.js` |
| Isotope | Network: `/assets/isotope.pkgd.min-ba3b4af1600b522dcaf1c282cc25167d.js`; Console globals `Isotope`, `Outlayer`, `Masonry` |
| toastr 2.1.0 | Console: `toastr.version` = `2.1.0` |
| jQuery Toast Plugin | Network: `/assets/jquery_toast/jquery.toast-e6f482ca469096468b65a10857d19fa6.js` and `.css` |
| Swiper | Console global `Swiper` |
| Magnific Popup | Network: stylesheet `/assets/magnific-popup-bd76b2742b05e58017f0fe452a612c2c.css` (CSS file loaded; JS not checked separately) |
| Animate.css | Network: stylesheet `/assets/animate-ca9c031f7663c9e52b815d368f541c0e.css` |
| Drouot (auction platform) | Network: scripts `cdn.drouot.com/assets?name=cpg/plugins-...js`, `cpg/functions-...js`, `cpg/cookiechoices-...js`, sale images `cdn.drouot.com/d/vente/...`; Page source: "DROUOT LIVE" link to `drouot.com/fr` |
| Let's Encrypt | Certificate: Issued by O = Let's Encrypt, CN = YR1 |

WordPress plugins / themes / Shopify apps:
- none (not WordPress)

After consent only:
- not checked

DNS only:
- MX: `mx4.mail.ovh.net` (1), `mx3.mail.ovh.net` (10) -> OVHcloud email
- TXT / SPF: no SPF; only TXT `1|www.loizillon.com`
- NS: `ns18.ovh.net`, `dns18.ovh.net` -> OVHcloud DNS
- DMARC: none (`_dmarc.loizillon.com` does not exist)

Notes (anything odd):
- Web server: NOT counted. No `server` or `x-powered-by` header on any response (redirect, page, robots.txt).
- Google Analytics: the gtag.js script loads and runs, but consent defaults to `denied` and the page sets `window['ga-disable-G-HCJ5MXKWJ2'] = true` before consent; no request to `google-analytics.com` and no `_ga` cookie was seen. The global `google_tag_manager` exists, but it comes from gtag.js itself: there is no `GTM-` container ID on the page, so Google Tag Manager is NOT counted.
- Slider Revolution: NOT counted. Its CSS is linked (`cdn.drouot.com/assets?name=revolution_slider/css/settings.css`, comment `<!-- REVOLUTION SLIDER -->`), but no Slider Revolution script or global (`revapi*`, `RS`) runs.
- Probably Grails asset-pipeline (a JVM asset bundler): globals `_asset_pipeline_require`, `_asset_pipeline_modules` and digest-suffixed file names (`style-6f91c0476104ee16f6fea9387a0decc9.css`). Fits the `JSESSIONID` cookie, but I could not confirm the global names on the vendor's site, so not counted.
- Globals `SEMICOLON`, `ssc_*` (smooth scroll), `jRespond`, `Stellar` look like the "Canvas" HTML template by SemiColonWeb; not confirmed, not counted.
- `medias-static-sitescp.zonesecure.org` serves the logo, favicon and a site CSS file (`/sitegenerique/40/css/cssFile20251027145014.css`); web search did not identify who runs it. The `40` in its path matches the `40` in the Drouot image paths, so it is likely the same auction-site platform. Not counted.
- `onYouTubeIframeAPIReady` / `ytp` globals exist, but no YouTube request was made: YouTube NOT counted.
- Certificate subject is `www.68-art.com`, not loizillon.com (a shared certificate; the browser accepted it, so loizillon.com is in its alternative names).
- robots.txt exists (blocks `/recherche/*` and a long list of bots).
- Mixed content warnings: `http://www.loizillon.com/uploads/Image/img_site/logo_footer.png` upgraded to HTTPS.

## seringa-safaris.com
- Checked: 2026-11-20, headless Chromium + curl + Resolve-DnsName, before consent
- Final URL: https://seringasafaris.com/        Redirected: yes, to a different domain: `https://seringa-safaris.com/` -> 301 (`server: Apache`) `https://seringasafaris.com/`
- Status: works
- Cookie banner: no (none visible in the screenshot)

| Technology | Where I saw it (tab + exact value) |
|---|---|
| Cloudflare | Headers (final page): `server: cloudflare`, `cf-ray: a5d723d50a5cb8d9-OTP`, `cf-cache-status: HIT`; Cookies `__cf_bm`, `cf_clearance` (domain `.seringasafaris.com`) |
| Cloudflare Bot Management (JavaScript detections) | Page source: inline script setting `window.__CF$cv$params` and loading `/cdn-cgi/challenge-platform/scripts/jsd/main.js?ray=...`; Network: that script + xhr `/cdn-cgi/challenge-platform/h/b/jsd/oneshot/...`; Cookie `cf_clearance` |
| Google Analytics (GA4, via gtag.js) | Network: script `www.googletagmanager.com/gtag/js?id=G-3E8YXZ8CD1`, fetch `region1.google-analytics.com/g/collect?v=2&tid=G-3E8YXZ8CD1...`; Cookies `_ga`, `_ga_3E8YXZ8CD1`; Console: `typeof gtag` = `function`, `dataLayer` has `config G-3E8YXZ8CD1` |
| Google Maps | Page source: `<iframe ... src="https://www.google.com/maps?q=-23.991111,27.955556&z=8&output=embed">`; Network (inside the iframe): `maps.googleapis.com/maps/api/js?key=...`, tiles `www.google.com/maps/vt?...` |
| Apache HTTP Server | Headers (first redirect only, on seringa-safaris.com): `server: Apache` |
| Google Trust Services | Certificate (seringasafaris.com): Issued by O = Google Trust Services, CN = WE1 |

WordPress plugins / themes / Shopify apps:
- none (not WordPress)

After consent only:
- not checked

DNS only:
- seringa-safaris.com (the input domain):
  - MX: `seringa-safaris.com` (0): mail on the domain itself
  - TXT / SPF: `v=spf1 ip4:184.173.239.119 a mx include:websitewelcome.com ~all` -> HostGator / websitewelcome mail; `google-site-verification=Ip-2rw...`
  - NS: `ns2091.hostgator.com`, `ns2092.hostgator.com` -> HostGator DNS
  - DMARC: none
- seringasafaris.com (the redirect target):
  - MX: `seringasafaris-com.mail.protection.outlook.com` -> Microsoft 365
  - TXT / SPF: `v=spf1 include:spf.protection.outlook.com -all` (Microsoft 365); `v=verifydomain MS=7151509`; `google-site-verification=AAvxb0...`
  - NS: `ns43.domaincontrol.com`, `ns44.domaincontrol.com` -> GoDaddy DNS
  - DMARC: `v=DMARC1; p=quarantine; adkim=r; aspf=r; rua=mailto:dmarc_rua@onsecureserver.net;`

Notes (anything odd):
- Two different sites: the input domain only redirects (Apache, HostGator). Everything else in the table is from seringasafaris.com.
- Built with Vite (React Server Components plugin), but NOT counted as running: the stylesheet tag has `data-precedence="vite-rsc/importer-resources"` and a Vite-style hashed name `assets/index-GKDcegjs.css`, yet no React or app bundle is loaded in the browser (`window.React` undefined, only `/assets/seringa-privacy.js?v=74` runs). The page is pre-rendered static HTML.
- Google Analytics is loaded by the site's own `/assets/seringa-privacy.js`, not from the HTML. It sends data and sets `_ga` before any consent (with `npa=1`, `allow_google_signals: false`).
- Google Fonts: requests to `fonts.googleapis.com` / `fonts.gstatic.com` come only from inside the Google Maps iframe (Roboto, Google Sans), not from the site itself. Not counted separately.
- Hosting: probably Cloudflare Pages (`cache-control: public, max-age=0, must-revalidate` plus a quoted MD5 `etag` on robots.txt), but nothing names it. Not counted.
- `onYouTubeIframeAPIReady` global exists but no YouTube request: YouTube NOT counted.
- robots.txt: `User-Agent: * / Allow: /`, `Sitemap: https://seringasafaris.com/sitemap.xml`.

## massageforworldpeace.com
- Checked: 2026-11-20, headless Chromium + curl + Resolve-DnsName, before consent
- Final URL: https://www.massageforworldpeace.com/        Redirected: yes, `https://massageforworldpeace.com/` -> 301 `https://www.massageforworldpeace.com`
- Status: works
- Cookie banner: no

| Technology | Where I saw it (tab + exact value) |
|---|---|
| IONOS MyWebsite (website builder) | Page source: `<meta name="generator" content="IONOS MyWebsite"/>`, footer link `IONOS MyWebsite` -> `ionos.com/websites/website-builder`, "Edit page" link to `203.sb.mywebsite-editor.com/app/682846652/...`; Network: `cdn.initial-website.com/templates/2118/style.css`, `cdn.initial-website.com/s/build/web.bundle.js`, `203.sb.mywebsite-editor.com/app/logstate-js.php?site=682846652`; Cookie `DIY_SB`; Console globals `diy`, `diysdk`, `jimdoData` |
| Apache HTTP Server | Headers (redirect and page): `server: Apache` |
| jQuery 1.11.0 | Console: `jQuery.fn.jquery` = `1.11.0` |
| Modernizr 2.8.3 | Console: `Modernizr._version` = `2.8.3` |
| RequireJS 1.0.7 | Console: `requirejs.version` = `1.0.7`, globals `require`, `define` |
| Hammer.js | Console global `Hammer` |
| Sectigo | Certificate: Issued by O = Sectigo Limited, CN = Sectigo Public Server Authentication CA DV R36 (wildcard `*.massageforworldpeace.com`) |

WordPress plugins / themes / Shopify apps:
- none (not WordPress). IONOS MyWebsite template `2118` (from `cdn.initial-website.com/templates/2118/style.css`)

After consent only:
- not checked

DNS only:
- MX: `aspmx.l.google.com` (1) + `alt1..alt4.aspmx.l.google.com` -> Google Workspace
- TXT / SPF: `v=spf1 include:_spf.google.com ~all` (Google Workspace); `google-site-verification=afyQ7j...`
- NS: `ns-us.1and1-dns.us/.com/.org/.de` -> IONOS (1&1) DNS
- DMARC: none

Notes (anything odd):
- `jimdoData` global and `diy*` names: IONOS MyWebsite looks like it is built on Jimdo's engine, but the page never names Jimdo (generator says IONOS MyWebsite). Jimdo NOT counted as a separate technology.
- PHP: probably, NOT counted. The page has `expires: Thu, 19 Nov 1981 08:52:00 GMT` (the value PHP sessions send by default) and the editor host serves `.php` files, but no header names PHP.
- `x-ws-origin`, `x-ws-ratelimit-*` headers: IONOS platform headers, they name no product.
- `content-security-policy-report-only` and `x-xss-protection` report to `cdn.initial-website.com/app/reporting/policyviolation/submit`: platform reporting, not a separate technology.
- No analytics: `dataLayer`, `gtag`, `ga`, `fbq` all undefined; only cookie is `DIY_SB`.
- A shopping-basket module is loaded (`moduleserver/js/en_US/common,shoppingbasket`, global `Shoppingbasket`) but the page shows no shop. Part of the builder, not counted separately.
- robots.txt: `Disallow: /c/`, `Disallow: /protected/`, `Sitemap: .../sitemap.xml`.

## nevsproductions.com
- Checked: 2026-11-20, headless Chromium + curl + Resolve-DnsName, before consent
- Final URL: none        Redirected: yes, `https://nevsproductions.com/` -> 301 (`server: Apache`) `https://www.nevsproductions.com`, which fails
- Status: broken (no website is served: the bare domain only redirects to www, and www points to Odoo, which has no site set up for this domain; a visitor sees a certificate warning, net::ERR_CERT_COMMON_NAME_INVALID)
- Cookie banner: no (no page)

Tried, as the steps say:
- `https://nevsproductions.com/` -> 301 to `https://www.nevsproductions.com` (Apache)
- `https://www.nevsproductions.com/` -> certificate error: the server sends a certificate for `*.odoo.com` / `odoo.com` (Let's Encrypt YR1), not for this domain
- `http://nevsproductions.com/` -> 301 to `https://www.nevsproductions.com` (same dead end)
- `http://www.nevsproductions.com/` -> 404 (`server: nginx`), body only: `<script>window.location = 'https://www.odoo.com/typo?domain=www.nevsproductions.com&autodbname=www&hosting=eu930a.odoo.com';</script>` (Odoo's "unknown domain" page)

| Technology | Where I saw it (tab + exact value) |
|---|---|

(empty: no website is served, so there is nothing for the tool to find. What answered is listed in the notes.)

WordPress plugins / themes / Shopify apps:
- none seen

After consent only:
- not checked

DNS only:
- MX: `smtp.rzone.de` (5) -> Strato mail
- TXT / SPF: no TXT records
- NS: `docks04.rzone.de`, `shades17.rzone.de` -> Strato DNS
- DMARC: `v=DMARC1;p=reject;`
- A: bare domain `217.160.0.205`; www is a CNAME to `nevsproductions.odoo.com` (`79.137.6.55`)

Notes (anything odd):
- The domain is half-moved: the bare domain still answers from an Apache redirect server (headers `x-ws-origin`, `x-ws-ratelimit-*`, the same ones seen on the IONOS-hosted massageforworldpeace.com), while www points to Odoo, where no Odoo database is set up for this domain. A visitor only sees a certificate warning.
- What answered, NOT counted because no website was served: Apache (headers on the bare domain's redirect, https and http: `server: Apache`); Odoo (DNS `www.nevsproductions.com` CNAME `nevsproductions.odoo.com`, certificate on www `CN=*.odoo.com`, the http://www body redirects to `www.odoo.com/typo?domain=www.nevsproductions.com...&hosting=eu930a.odoo.com`); nginx (headers on `http://www.nevsproductions.com/`, the Odoo endpoint: `server: nginx`).

## littlerockafamily.de
- Checked: 2026-11-20, headless Chromium + curl + Resolve-DnsName, before consent
- Final URL: none        Redirected: no
- Status: dead (net::ERR_NAME_NOT_RESOLVED)
- Cookie banner: no (no page)

Tried: `https://littlerockafamily.de/` (ERR_NAME_NOT_RESOLVED), `http://littlerockafamily.de/`, `https://www.littlerockafamily.de/`, `http://www.littlerockafamily.de/` (curl: "Could not resolve host" for all).

| Technology | Where I saw it (tab + exact value) |
|---|---|

WordPress plugins / themes / Shopify apps:
- none

After consent only:
- not checked

DNS only:
- MX: none
- TXT / SPF: none
- NS: none
- `Resolve-DnsName` says "DNS name does not exist" (NXDOMAIN) for the domain, for www, for MX / TXT / NS / `_dmarc`, and also when asking 8.8.8.8 for the SOA: the domain is not registered or not delegated.

Notes (anything odd):
- No technologies: nothing answers.

## appelpsychotherapy.com
- Checked: 2026-11-20, headless Chromium + curl + Resolve-DnsName, before consent
- Final URL: https://www.appelpsychotherapy.com/        Redirected: yes, `https://appelpsychotherapy.com/` -> 301 (`server: Pepyaka`) `https://www.appelpsychotherapy.com/`
- Status: works
- Cookie banner: no

| Technology | Where I saw it (tab + exact value) |
|---|---|
| Wix | Page source: `<meta name="generator" content="Wix.com Website Builder"/>`; Headers: `server: Pepyaka`, `x-wix-request-id`, `x-meta-site-id` (redirect); Network: `static.parastorage.com/services/wix-thunderbolt/dist/main.109d605e.bundle.min.js`, images from `static.wixstatic.com`; Cookies `svSession`, `bSession`, `hs`, `XSRF-TOKEN`; Console globals `thunderboltVersion`, `wixTagManager`, `viewerModel` |
| Cloudflare | Headers (final page): `server: cloudflare`, `cf-ray: a5d7289fda48a829-OTP`, `cf-cache-status: HIT`; Cookie `__cf_bm` |
| Varnish | Headers: `server-timing: cache;desc=hit, varnish;desc=hit, dc;desc=ireland-phy` (redirect); `via: 1.1 varnish (Varnish/6.0)` on the Wix map document from `static.parastorage.com` |
| React 18.3.1 | Console: `React.version` = `18.3.1`, `ReactDOM.version` = `18.3.1-next-f1338f8080-20240426`; Network: `static.parastorage.com/unpkg/react@18.3.1/umd/react.production.min.js` |
| Lodash 4.18.1 | Console: `_.VERSION` = `4.18.1`; Network: `static.parastorage.com/unpkg/lodash@4.18.1/lodash.min.js` |
| RequireJS 2.3.6 | Console: `requirejs.version` = `2.3.6`; Network: `static.parastorage.com/unpkg/requirejs-bolt@2.3.6/requirejs.min.js` |
| Sentry 7.120.3 | Network: `browser.sentry-cdn.com/7.120.3/bundle.tracing.es5.min.js`; Console globals `Sentry`, `__SENTRY__` |
| Google Analytics (GA4, via gtag.js) | Network: `www.googletagmanager.com/gtag/js?id=G-S4YD61CZTN`, fetch `region1.google-analytics.com/g/collect?v=2&tid=G-S4YD61CZTN&...&gcs=G100...`; Console: `dataLayer` has `config G-S4YD61CZTN` |
| Google Ads (conversion tracking) | Network: `www.googletagmanager.com/gtag/js?id=AW-17813400584`, fetch `pagead2.googlesyndication.com/ccm/collect?...tid=AW-17813400584&en=page_view...`; Console: `dataLayer` has `config AW-17813400584` |
| Google Consent Mode | Console: `dataLayer[0]` = `consent default` with `ad_storage` / `analytics_storage` = `denied` |
| Google Maps | Network (inside the Wix map iframe `static.parastorage.com/.../googleMap.f3347ff6.html`): `maps.googleapis.com/maps/api/js?callback=initMap&key=...`; Console log `Google Maps API version: 3.67.1d` |
| Psychology Today (verified seal widget) | Network: `member.psychologytoday.com/verified-seal.js`, `d3mmydk2yvkj9n.cloudfront.net/prod/verified-seal/latest/verified-seal-cb.min.js`, `www.psychologytoday.com/api/verified-seal/seals/14/profile/785438?callback=sxcallback`; seal "Verified by Psychology Today" visible in the screenshot |
| Let's Encrypt | Certificate: Issued by O = Let's Encrypt, CN = YR2 |

WordPress plugins / themes / Shopify apps:
- none (Wix). Wix apps seen: Wix FAQ (`editor.wixapps.net/render/prod/editor/faq-ooi/1.693.0/FaqOoi`, xhr `/_api/faq-server/v2/question-entries/query`); Wix Forms (`static.parastorage.com/services/forms-viewer/1.883.0/viewerScript.bundle.min.js`); Wix Members / profile card (`santa-members-viewer-app`, `profile-card-tpa-ooi`); Wix Bookings (page data `"appDefinitionName":"Wix Bookings"`, its app ID `13d21c63-...` in the `siteassets.parastorage.com` requests)

After consent only:
- not checked

DNS only:
- MX: `aspmx.l.google.com` (10) -> Google Workspace
- TXT / SPF: `v=spf1 include:_spf.google.com ~all`; `google-site-verification=5VjR-...`
- NS: `ns2.wixdns.net`, `ns3.wixdns.net` -> Wix DNS
- DMARC: CNAME to `_dmarc.wixemails.com` -> `v=DMARC1; p=none; rua=mailto:dmarc_agg@vali.email`

Notes (anything odd):
- `cdn.pagebooster.net/optimizer/appelpsychotherapy.com---284703ad-....js` loads first (added as Wix custom code: page data `"type":"custom","html":"<script src=\"https://cdn.pagebooster.net/...`). Web search found nothing; its own homepage is a shared landing page for a family of SEO tool sites (`links4u.net`, `seoly.co`, `easyseo.shop`, `seorush.me`, `pagebooster.net`...). Looks like an SEO add-on; NOT counted because I could not name the product reliably.
- Two React versions: the Wix FAQ iframe (`editor.wixapps.net`) loads its own `react@16.13.0` and `lodash@4.17.15`. The table lists the main page's versions.
- Google Analytics / Google Ads still send cookieless pings before consent (`gcs=G100`, `npa=1`); no `_ga` cookie is set.
- Facebook / Instagram icons are links only; `fbq` undefined.
- Wix Stores cart code is referenced in the page data (`wixstores-client-cart-ooi/1.6484.0`) but not loaded: NOT counted.
- `onYouTubeIframeAPIReady` global exists but no YouTube request: YouTube NOT counted.
- robots.txt: Wix's standard file (`Disallow: *?lightbox=`, rules for `AdsBot-Google`, blocks `PetalBot`).
- Suppliers' infrastructure, NOT counted as the site's technologies: Amazon CloudFront (headers `via: ...cloudfront.net (CloudFront)`, `x-cache: Hit from cloudfront` on the Wix custom-HTML iframes `www-appelpsychotherapy-com.filesusr.com/html/...`, and the Psychology Today seal script served from `*.cloudfront.net`) and Amazon S3 (the TherapyDen badge image `s3-us-west-2.amazonaws.com/therapyden1/logos/therapyden-web-badge.png`, hosted by TherapyDen). They are Wix's, Psychology Today's and TherapyDen's delivery, not choices of this site.

## 1stlocksheathscouts.org.uk
- Checked: 2026-11-20, headless Chromium + curl + Resolve-DnsName, before consent
- Final URL: https://1stlocksheathscouts.org.uk/        Redirected: browser: 307 to the same URL (Cloudflare challenge loop); curl: no
- Status: bot check for the headless browser ("Performing security verification", Cloudflare Turnstile "Verify you are human", HTTP 403); curl received the real page (HTTP 200, "Home - 1st Locksheath Scout Group"). Not bypassed; everything below except the Cloudflare rows comes from curl (HTML before JavaScript, headers, robots.txt).
- Cookie banner: not checked (the browser never reached the page)

| Technology | Where I saw it (tab + exact value) |
|---|---|
| WordPress 7.1.5 | Page source (curl): `<meta name="generator" content="WordPress 7.1.5" />`; Headers: `link: <https://1stlocksheathscouts.org.uk/wp-json/>; rel="https://api.w.org/"`; robots.txt `Disallow: /wp-admin/`, `Allow: /wp-admin/admin-ajax.php` |
| Cloudflare | Headers: `server: cloudflare`, `cf-ray: a5d72bcf2e167607-OTP`, `cf-cache-status: EXPIRED`; Cookies `__cf_bm`, `_cfuvid` |
| Cloudflare Bot Management (challenge page) | Browser: 403 page "Performing security verification", script `/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1?ray=...`, Console global `_cf_chl_opt` |
| Cloudflare Turnstile | Browser (challenge page): `challenges.cloudflare.com/turnstile/v0/b/f8df4d4a0cd6/api.js`, Console global `turnstile`, widget "Verify you are human" in the screenshot |
| Cloudflare Email Address Obfuscation | Page source (curl): script `/cdn-cgi/scripts/5c5dd728/cloudflare-static/email-decode.min.js` |
| The Events Calendar | Headers: `x-tec-api-root: https://1stlocksheathscouts.org.uk/wp-json/tribe/events/v1/`, `x-tec-api-version: v1`; Page source: `/wp-content/plugins/the-events-calendar/common/build/js/user-agent.js`, feed link `/events/?ical=1` |
| Font Awesome 4.7.0 | Page source: stylesheet `maxcdn.bootstrapcdn.com/font-awesome/4.7.0/css/font-awesome.min.css?ver=4.7.0` |
| BootstrapCDN | Page source: see Font Awesome row (`maxcdn.bootstrapcdn.com`) |
| Bluehost (WordPress hosting plugin) | Page source: `/wp-content/plugins/bluehost-wordpress-plugin/vendor/newfold-labs/wp-module-performance/build/assets/link-prefetch.min.js?ver=5.1.1`; Headers: `x-endurance-cache-level: 0` |
| Google Trust Services | Certificate: Issued by O = Google Trust Services, CN = WE1 |

WordPress plugins / themes / Shopify apps:
- The Events Calendar (plugin `the-events-calendar`): see table
- Bluehost (plugin `bluehost-wordpress-plugin`, Newfold Labs performance module, link prefetch): see table
- Skills for Life (theme `skillsforlife`): Page source `/wp-content/themes/skillsforlife/style.css?ver=1791042255`, `.../js/scripts.js`, Scouts logo `.../img/Scouts-Logo-Stack-White.svg`

After consent only:
- not checked

DNS only:
- MX: `1stlocksheathscouts-org-uk.mail.protection.outlook.com` -> Microsoft 365
- TXT / SPF: `v=spf1 include:spf.protection.outlook.com +a +mx +ip4:173.254.28.106 -all`; `MS=ms12797387`
- NS: `ns1.justhost.com`, `ns2.justhost.com` -> Just Host (hosting / DNS)
- DMARC: `v=DMARC1;p=quarantine;rua=mailto:dmarc@1stlocksheathscouts.org.uk`

Notes (anything odd):
- The challenge is shown to headless Chromium only; curl with a normal browser user-agent got the page. Console checks, cookies set by page scripts and third-party requests could NOT be checked: anything loaded only by JavaScript (analytics, embeds) may be missing from this label.
- No analytics in the static HTML: no `gtag(`, `GTM-`, `UA-`, `fbq(`.
- No jQuery script in the static HTML.
- Theme version unknown: `?ver=1791042255` is a timestamp, not a version.
- `x-nginx-cache: WordPress` header suggests nginx as a cache at the host, but no `server` header names it (Cloudflare replaces it). NOT counted.
- Hosting: Bluehost plugin + `x-endurance-cache-level` + Just Host DNS point to a Newfold Digital host (Bluehost / Just Host); which one is not proven.
- Social share icons (Facebook, X, LinkedIn, Pinterest, WhatsApp) are links, not trackers.

## rivithead.com
- Checked: 2026-11-20, headless Chromium + curl + Resolve-DnsName, before consent
- Final URL: https://www.rivithead.com/        Redirected: yes, `https://rivithead.com/` -> 301 `https://www.rivithead.com/`
- Status: works (online shop for gothic clothing)
- Cookie banner: no

| Technology | Where I saw it (tab + exact value) |
|---|---|
| Apache HTTP Server | Headers (redirect, page, robots.txt): `server: Apache` |
| PHP | Page source: `<form name="login" action="login.php?action=process" method="post">`; robots.txt `Disallow: /share_box_frame.php`, `/share_email.php`; Headers: `expires: Thu, 19 Nov 1981 08:52:00 GMT` (PHP session default) |
| Matomo (self-hosted) | Page source: `_paq.push(['setTrackerUrl', u+'matomo.php'])`, `g.src=u+'includes/javascript/matomo.js'`; Network: script `www.rivithead.com/includes/javascript/matomo.js`, ping `www.rivithead.com/matomo.php?action_name=...&idsite=1&rec=1...`; Cookies `_pk_id.1.d3ba`, `_pk_ses.1.d3ba`; Console globals `_paq`, `Matomo`, `Piwik` |
| Swiper 12.1.4 | Network: `images.rivithead.com/includes/javascript/swiper-bundle.12.1.4.min.js`, `.../css/swiper-bundle.min.1.2.css`; Console global `Swiper` |
| Google Fonts | Network: stylesheet `fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&display=swap`, font `fonts.gstatic.com/s/rajdhani/...` |
| Sectigo | Certificate: Issued by Sectigo Public Server Authentication CA OV R36 (organisation-validated) |

WordPress plugins / themes / Shopify apps:
- none (not WordPress / Shopify). Own layout `Rivithead25` (`images.rivithead.com/layout/Rivithead25/css/...`)

After consent only:
- not checked

DNS only:
- MX: `mail.rivithead.com` (10): mail on the site's own domain
- TXT / SPF: `v=spf1 a mx -all`; `google-site-verification=oT35uA...`
- NS: `ns-989.awsdns-59.net`, `ns-131.awsdns-16.com`, `ns-1626.awsdns-11.co.uk`, `ns-1061.awsdns-04.org` -> Amazon Route 53
- DMARC: `v=DMARC1; p=reject; rua=mailto:blue@rivithead.com; ...`

Notes (anything odd):
- Shop software: unknown. The pattern `login.php?action=process`, "My Account / Cart Contents / Checkout" and `share_email.php` looks like osCommerce, but no generator tag, file path or cookie names it (the session cookie is renamed to `rhud`). NOT counted.
- Static files come from the site's own subdomain `images.rivithead.com` (not a third-party CDN).
- Cookie `theme=darkmode` is the site's own dark-mode setting.
- No Google Analytics / Tag Manager / Facebook Pixel: `dataLayer`, `gtag`, `ga`, `fbq` all undefined.
- robots.txt exists (sitemaps, blocks AhrefsBot, BLEXBot, `/search`).
