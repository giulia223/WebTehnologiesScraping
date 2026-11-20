# Definitions: what counts as a technology a website uses

These definitions decide every judgement in this project: when a detection is labelled correct or wrong, when a rule is written or changed, and when a test states what the tool should report. Anyone who labels, writes rules, writes code or writes tests uses them, so that two people (or two automated reviewers) looking at the same evidence reach the same verdict.

## The question behind every verdict

**Does the organisation that owns this domain use this technology on this website?**

The output is a sales lead list: a salesperson reads "uses X" as "this company chose X, or runs it". A detection is only correct if that reading is true.

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

- **Not the site's:** the platform's web server, CDN, caching, storage, cloud provider, bot protection and certificate authority. Also the platform's own tracking, session and error monitoring that report to the platform, not to the owner, for example Weebly's Snowplow tracker, Wix's `svSession` cookie and Wix's Sentry. These detections are **wrong**, and the same items in a hand-made list are not counted for recall.
- **The site's:** the platform itself, apps the owner installed, page code that runs on the page (including libraries the platform bundles, such as React on Wix), web features, and the domain's own DNS services such as its mail provider.

The same test applies to a site the owner hosts themselves: their hosting company, server and CDN are their choice, so they count.

Two cases that look like infrastructure but are not the site's:
- **A third party's CDN used for one file.** A site that loads Font Awesome from cdnjs uses cdnjs. It does not use Cloudflare, the company that runs cdnjs.
- **A supplier's servers seen in a request.** A header on a request to another company's server describes that company, not the site.

## Evidence: used, not mentioned

| Verdict | When |
|---|---|
| **correct** | The evidence proves the technology is present and used on this site, as defined above. |
| **wrong** | The evidence does not prove it. Typical cases: the pattern matched something else (a CSS class name, another product with similar markers); the product is only **mentioned** (a word in the text, a domain listed in a security-policy header, a logo image, a name inside a platform's page data); code is referenced but never loaded; the technology belongs to a platform or supplier, not to the site; the detection depends on another detection that is wrong. |
| **unsure** | The saved evidence cannot settle it. For example: a tracker's placeholder function exists, but its script never loaded (it probably waits for cookie consent); a global variable that several libraries define. |

More rules:
- **Inferred detections** (reached only because another technology implies them) are correct only when the implying technology makes them certain. WordPress implies PHP, which is correct. WordPress implies MySQL, which is unsure, because it could be MariaDB. An inference from a wrong detection is wrong.
- **A wrong version does not make a detection wrong.** The technology is judged; the version error is noted separately.
- **Two rules for the same product** (for example "Typekit" and "Adobe Fonts") are judged one by one. Both can be correct. The duplication is a rule problem, reported apart from precision.
- **Unsure is not a hidden "wrong".** Unsure detections are left out of precision and reported with their count, so the reader sees how many were undecidable.

## For whoever writes rules, code or tests

- A rule that can only match a mention (a text word, a header that lists domains, an image) must not report "used".
- A rule must not credit a platform's or supplier's infrastructure to the site.
- A test that states "the tool should report X" follows these definitions. If a test and this file disagree, this file wins, and the test is fixed.
