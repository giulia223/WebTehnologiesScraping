## Checked by hand in a normal browser (2026-11-20)

Every failure group above was opened by hand. Where the result matched the tool's group it
is not repeated here; these are the differences and what they mean.

| Domain | Tool's group | Seen by a person | What it means |
|---|---|---|---|
| 1ststopsupply.com, aardvarkelectriccorp.com, 1stclairavewest.com | dead behind a CDN | Cloudflare error 1001 (DNS resolution error), connection not secure | confirmed: dead sites behind Cloudflare |
| gogroupauto.com, howellmotors.com, huberautomotive.com | bot wall | the site loads (gogroupauto and howellmotors only without a secure connection) | the wall blocks automated clients, not people |
| ecolab.com, roccofortehotels.cn, brucebournesold.com | bot wall (Akamai deny page) | the site loads | same: blocks only automated clients |
| aida-cruises.at | blocked 403, blocker not recognisable | the site loads | bot protection of an unknown kind; stays an HTTP error, since the page does not say who blocked |
| stwannas.com | bot wall (Cloudflare block page) | "Sorry, you have been blocked. You are unable to access dealercenterwebsite.net" | blocked for people here too; the site runs on the DealerCenter platform |
| fisherinvestments.com | blocked 403 | 403 for a person too | blocked from this location for everyone, not only for the tool |
| juanmunozhomesmart.com | bot wall (Akamai deny page) | "this site is currently under construction... is this your website?" | the wall hides a placeholder page |
| everhartsoutdoor.com | blocked 403 | the browser cannot make a secure connection | broken HTTPS set-up on the site |
| startravelcompany.com | no answer in time | HTTP 400 | the site answers people with an error: broken either way |
| branson-connection.com | parked (ad lander) | redirects to a fake "verify you are human" page that asks for key presses | a hijacked domain sending visitors to a malicious page (a known fake-CAPTCHA trick); never follow its instructions |
| maiquotes.net | redirected | "connection not secure" | its certificate is for another name (see below) |
| dioxoxigenio.com.br | placeholder (hosting default page) | **the company's real site** | **our bug**: the real site is only on `http://`; on `https://` the server shows a certificate for another name and the host's default page, which we took as the answer |
| den-a-sha.co.jp | placeholder ("invalid URL") | **a real site** | **our bug**: same as above; the real site is at `http://www.den-a-sha.co.jp/`, an address we never try |

**Our bug, in detail (now fixed):** when a site's certificate was made for another name, the fetcher
retried without certificate checks and accepted the page it got. On shared hosting that
page is often the host's default page, while the real site is served over plain `http://`.
6 domains were collected this way (amaronbattery.in, cmresolutions.com, den-a-sha.co.jp,
dioxoxigenio.com.br, maiquotes.net, wrinkle-pei.cz). Now such a page no longer ends the
search, `http://www.` is tried too, and all 6 give their real site.

**Changed since the first crawl:** weissbauchpapageien.de now lands on a "domain for sale"
page (domainname.de) instead of an ad lander: still parked, recognised by a new rule;
ferienwohnung-kastell.de stopped resolving.
