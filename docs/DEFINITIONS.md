# Definitions: what counts as a technology a website uses

These definitions decide every judgement in this project: when a detection is labelled correct or wrong, when a rule is written or changed, and when a test states what the tool should report. Anyone who labels, writes rules, writes code or writes tests uses them, so that two people (or two automated reviewers) looking at the same evidence reach the same verdict.

## The question behind every verdict

**Does the organisation that owns this domain use this technology on this website?**

The output is used to find sales leads: a salesperson reads "uses X" as "this company chose X, or runs it". A detection is only correct if that reading is true.

- **The site** is the organisation that owns the input domain. `insieme.stanford.edu` belongs to Stanford, so Stanford's hosting choices are the site's choices.
- **The evidence** is what the tool saved: the page, headers, cookies, requests, JavaScript globals, certificate, DNS records and robots.txt. A verdict is based on that evidence, not on what the site probably uses.

## Classes of technology

Every detected technology falls into one class. The class decides the test.

| Class | Examples | Counts when |
|---|---|---|
| **Website platform** | Wix, Weebly, Squarespace, Shopify, IONOS MyWebsite, WordPress | The site is built on it. |
| **Add-on chosen by the owner** | WordPress plugins and themes, Shopify and Wix apps | Its code or markup is on the page. |
| **Page code** | Libraries and frameworks (jQuery, React, Lodash), widgets, fonts, analytics and marketing tags | Its code is loaded and runs on the page, whoever loaded it: the owner, the platform or another widget. |
| **Infrastructure** | Web server (Apache, LiteSpeed), CDN (Cloudflare, CloudFront, cdnjs), caching (Varnish), load balancer, cloud storage (Amazon S3), cloud provider (AWS), bot protection, certificate authority, hosting company | **The site's owner chose it.** See "Hosted platforms" below. |
| **Web feature** | HSTS, HTTP/3, Open Graph tags, RSS feed, Priority Hints, a cart | It is present on the page or in its headers, whoever set it. A web feature is a property of the page, not a product anyone chose to buy. |
| **DNS-only service** | Mail provider (MX), DNS host (NS), a service with a TXT verification token | The record is the domain's own. A verification token proves the company has an account with that service. Reported in the separate DNS-only group. |

## Hosted platforms: whose infrastructure is it?

A site built on a hosted website builder (Wix, Weebly, Squarespace, IONOS MyWebsite, and similar) runs on the platform's servers. The platform, not the owner, picked those servers, the CDN in front of them, the storage behind them, the bot protection, the certificate and the platform's own monitoring. So on such a site:

- **Not the site's:** the platform's web server, CDN, caching, storage, cloud provider, bot protection and certificate authority. Also the platform's own tracking, session and error monitoring that report to the platform, not to the owner, for example Weebly's Snowplow tracker, Wix's `svSession` cookie and Wix's Sentry. The platform's server-side language and database (inferred from the platform, for example PHP and MySQL from Weebly) are its own too, and so is a hosting company's own cloud (AWS inferred from Acquia, Stanford's host). These detections are **wrong**, and the same items in a hand-made list are not counted for recall.
- **The site's:** the platform itself, apps the owner installed, page code that runs on the page (including libraries the platform bundles, such as React on Wix), web features, and the domain's own DNS services such as its mail provider.

The same test applies to a site the owner hosts themselves: their hosting company, server and CDN are their choice, so they count.

Two cases that look like infrastructure but are not the site's:
- **A third party's CDN used for one file.** A site that loads Font Awesome from cdnjs uses cdnjs. It does not use Cloudflare, the company that runs cdnjs.
- **A supplier's servers seen in a request.** A header on a request to another company's server describes that company, not the site.

## Evidence: used, not mentioned

| Verdict | When |
|---|---|
| **correct** | The evidence proves the technology is present and used on this site, as defined above. |
| **wrong** | The evidence does not prove it. Typical cases: the pattern matched something else (a CSS class name, another product with similar markers); the product is only **mentioned** (a word in the text, a name inside a platform's page data, a link to an article about it); code is referenced but never loaded; the technology belongs to a platform or supplier, not to the site; the detection depends on another detection that is wrong. |
| **unsure** | The saved evidence cannot settle it. For example: a tracker's placeholder function exists, but its script never loaded (it probably waits for cookie consent); a global variable that several libraries define. |

## How strict: follow Wappalyzer, except for another company's infrastructure

The rules come from Wappalyzer (now maintained as webappanalyzer), and its list sets the bar for what is a technology: every name it has counts, web features included. Its patterns are sometimes looser about the evidence than "seen running". We follow that looser reading whenever the evidence is **the site's own choice of that product**, and stay strict only when the evidence points to **another company's infrastructure**:

| Counted (the site chose the product) | Not counted (another company's infrastructure) |
|---|---|
| Google Tag Manager from `gtag.js` (the Google tag): the site added Google's tag | Cloudflare inferred from cdnjs: the site uses a free public CDN, it is not Cloudflare's customer |
| PWA from a web app manifest: the site declared itself installable | A cloud provider (Amazon S3, AWS) named only in a security-policy header: the host belongs to some vendor |
| A payment provider the site lists or allows: its logo among the accepted payment methods, its domain allowed in the site's security-policy header | A hosted platform's infrastructure (see above) |

A mention outside such a deliberate list (a word in an article, a logo in a blog post) is still not use.

More rules:
- **Inferred detections** (reached only because another technology implies them) are correct only when the implying technology makes them certain. WordPress implies PHP, which is correct. WordPress implies MySQL, which is unsure: the link is the rule set's convention, and the database could be MariaDB. An inference from a wrong detection is wrong. One implied by a hosted platform for its server side (PHP and MySQL from Weebly) is the platform's, not the site's.
- **A wrong version does not make a detection wrong.** The technology is judged; the version error is noted separately.
- **Two rules for the same product** (for example "Typekit" and "Adobe Fonts") are judged one by one. Both can be correct. The duplication is a rule problem, reported apart from precision.
- **Unsure is not a hidden "wrong".** Unsure detections are left out of precision and reported with their count, so the reader sees how many were undecidable.

## For whoever writes rules, code or tests

- A rule that can only match a mention (a word in the text, a name in someone else's data) must not report "used". A product the site lists or allows on purpose (payment logos, a security-policy entry naming that product's own domain) is the site's choice and counts; see "How strict" above.
- A rule must not credit a platform's or supplier's infrastructure to the site.
- The detector reports a hosted platform's infrastructure in its own group, `platform_infra`, not as the site's technologies; this includes what the platform implies for its server side. Precision and recall leave that group out.
- The headline count (core) is the technologies used to build the website: themes, plugins and apps included, and implied technologies included (they keep tier `inferred`). DNS-only services (mail, name servers, verification tokens) are the company's, not the website's, and are reported apart.
- A test that states "the tool should report X" follows these definitions. If a test and this file disagree, this file wins, and the test is fixed.
