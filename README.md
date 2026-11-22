# techdetect

A company develops an analytics plugin for Shopify stores. Their salespeople want to find websites that are using competing products in order to create a marketing campaign to convince the owners to migrate to their solution.

Goal: a tool that identifies technologies used in building a website.

## The problem as I understood it

A salesperson will read my output as "this company uses X", so for me a smaller list that is right beats a bigger list with guesses in it. That's the base I've built, and from there, scaling and improving is needed.

What counts as "a technology the site uses" turned out to be the hardest question, and I wrote the answer down in [`docs/DEFINITIONS.md`](docs/DEFINITIONS.md).
The short version: if a site is built on Wix, Wix's servers, CDN and certificate are Wix's choice, not the owner's, so I report them apart. A product that is only mentioned (a word in the text, a URL in a list of blocked vendors, an icon of a technology) is not used.

Every detection in the output carries its evidence (which rule, which file, the exact text that matched, and the URL), so anyone can check it in their own browser.

## Results


|  | Domains |
|---|---|
| Working website  | **154** |
| Not reachable: the name doesn't resolve (8 dead, 3 dead behind a CDN) | 11 |
| Not reachable: HTTP error (12 broken or closed, 3 blocked with a 403 by an unknown blocker) | 15 |
| Not reachable: no answer in time | 2 |
| Bot check | 10 |
| Parked or for sale | 3 |
| Placeholder page (hosting default, "coming soon") | 2 |
| Redirects to a different company's site | 3 |
| **Total** | **200** |

The 46 domains in the other rows are left out of the counts, but each still has a line in the output saying why.

What the tool found on the 154 working sites:
| | |
|---|---|
| Technologies used to build the sites (themes, plugins and implied ones included) | **347** |
| Also reported, kept apart | 38 found only in DNS (mail provider, name servers, verification tokens) and 10 that only ever belong to a hosted platform's infrastructure |
| Total if you add those | 395 |
| Average per working site | 15.4 |
| Precision (20 labelled sites, every detection judged) | 99.7% (320/321) |
| Recall (10 sites labelled blind, before I saw the tool's output for them) | 74% (84/113), and 97% (84/87) where a rule exists. Before the 9 rules I added for misses on these same 10 sites: 66% (75/113); the gain is fitted to this set, not an independent measurement |


The precision number only covers the 20 labelled sites, and those mostly contain common things like jQuery and WordPress. The count I present is a count of names, so I also checked every name once: 140 were judged on the labelled sites, each of the other 209 got one random example ([`docs/NAME_CHECK.md`](docs/NAME_CHECK.md)). That check found real problems the 99% had hidden. Rare names were wrong much more often (Livefyre was really Leaflet, "Lightbox" was any file with lightbox in its name). I fixed the worst rules in two rounds and the count went from 341 to 322 (324 after the rules for known misses, below; 349 once themes and implied technologies count, 347 after the last two fixes). The second round switched off the patterns behind 9 wrong names (Matter.js matching `formatter.js`, Facebook Login read from any Facebook SDK, eWAY matching the host 2agateway.com...), took out my own stricter Lightbox rule, which still matched another builder's `lightbox.js`, and stopped counting a detection whose clues the rule author all marked confidence 0 (how Livefyre got in; it also removed Duda, which was right). 7 names judged wrong are still in the output (Adobe Experience Manager, Bitmovin, wetter.com...), because their evidence is real but misleading (a copied page, a consent blocker's placeholder, a script that failed to load) and no narrow rule fix exists. The last check, of the names that became countable when themes and implied technologies joined the count, found 2 more wrong, from rule problems I then fixed with two more corrections: Cookie Information was implied by a WordPress consent plugin that works without that service, and Signify Dark was read from a setting every Signify theme defines (the site runs Signify Pro). On the names checked this way precision is 96% (192/199).

### Who judged

The verdicts were made by separate AI reviewer agents, working only from the saved evidence and the written definitions, not by the code that made the detections. As a second judge, I opened a few random domains myself and checked that the results match what I see.

For recall, I took 10 domains (every 20th row of the list, before I saw the tool's output for them), inspected each one in the browser and wrote down every technology I could confirm. The tool's results were compared against that list.

What I checked on each site:
- the final URL (any redirect?) and whether there is a cookie banner; if it didn't load, I tried `http://` and `https://www.`
- the response headers
- the third-party domains in the Network tab, the most useful step (right-click a column header and enable Domain)
- the page source, searching for keywords
- JavaScript variables in the console
- cookies

My 347 counts the technologies used to build the website, from evidence on the site itself, one page per site, at one moment: themes, plugins and technologies implied by another one (WordPress implies PHP) included, a hosted platform's own server side (PHP and MySQL implied by Weebly) not. 

Adding the DNS-only services and the hosted platforms' infrastructure, every group as unique names, gives 395. The things that would raise it are in the last section.

## How it works

```
domains
      |
      v
  collect   (network, slow, run once)        data/raw/<domain>/
      |      plain HTTP fetch                  static.json + static.html
      |      DNS, TLS certificate, robots.txt  dns.json, tls.json, wellknown.json
      |      headless Chromium                 browser.json + rendered.html + screenshot
      v
  detect    (run local)                      data/output/
      |      technology rules                   detections.jsonl 
      |      (open Wappalyzer rules)            technologies.csv 
      |                                        
      v
  evaluate                                  docs/METRICS.md, ABLATION.md, TIERING.md
```

The most important decision was to split collecting from detecting. Collecting 200 sites takes time and touches the network. Detecting runs on the saved files in seconds. So I could change a rule, re-run, and see exactly what changed, hundreds of times, without visiting any site again. 

The rules come from [webappanalyzer](https://github.com/enthec/webappanalyzer), the maintained continuation of Wappalyzer's open rule set. I wrote my own matching engine instead of using a library, because the libraries return a name without the proof, and the proof was the point. The engine follows how Wappalyzer itself matches, implies and excludes, so its results are comparable.

On top of the upstream rules there are reviewed corrections and 12 rules of my own (9 of them for technologies the blind inspection found and the tool missed, each match checked by a separate judge).

## Run it

```
pip install -e ".[dev]"
playwright install chromium

techdetect collect                 # all 200 domains, about 13 minutes, writes data/raw/
techdetect detect                  # offline, writes data/output/
techdetect evaluate metrics        # precision and recall from data/ground_truth.json
techdetect evaluate ablation       # what each collection step adds
techdetect evaluate tiering        # browser only where it pays
pytest
```



## Output

The result is [`data/output/technologies.csv`](data/output/technologies.csv) (and `.parquet`): each domain and its technologies, one row per domain and technology, with categories, version, confidence and group. 

It holds only the domains that are counted, so its unique core names are the headline number; a domain that redirects to another company, or a parked or placeholder page, is left out there but keeps its line, with its status, in detections.jsonl. 
It covers 177 domains: the 154 working sites, plus 23 unreachable or bot-checked ones whose DNS or certificate still gave facts. The separate group counts in summary.json leave out names already counted in core, so a per-group unique count on the CSV is higher; only core matches a plain count. 

summary.json's `extended_total` (385) is core plus DNS-only; adding the 10 platform-infrastructure names gives the 395 above, which equals a plain count of all names in the CSV. [`data/output/summary.json`](data/output/summary.json) has the counts.

The proof is [`data/output/detections.jsonl`](data/output/detections.jsonl): one line per input domain, including the failed ones, with every piece of evidence. One piece of evidence for Shopify on herbalwise.ie:

```json
{
  "name": "Shopify", "confidence": 100, "tier": "observed", "group": "core",
  "signal": "scriptSrc", "pattern": "cdn\\.shopify\\.com",
  "source_artifact": "static.html",
  "location": "https://cdn.shopify.com/shopifycloud/shop-js/modules/v2/loader.init-shop-cart-sy...",
  "matched_value": "cdn.shopify.com"
}
```

`tier` says what kind of proof it is: `observed` (seen working: a script, a request, a header), `declared` (the site states it: a generator tag, a DNS record), or `inferred` (only implied by another technology).


## What the browser is worth

I re-ran detection with only some of the collected data allowed, to see what each step adds ([`docs/ABLATION.md`](docs/ABLATION.md)):

| Data used | Technologies | Recall | Seconds per domain |
|---|---|---|---|
| plain HTTP page + certificate + robots.txt | 237 | 40% | 5.6 |
| + URLs inside the page's inline scripts | 250 | 40% | 5.6 |
| + script files downloaded (not run) | 267 | 40% | 12.5 |
| plain page + headless browser (without the two rows above) | 348 | 65% | 15.7 |
| + DNS (the full system) | 347 | 74% | 17.8 |

Recall here includes the 9 rules fitted to the blind set (see Results).

The browser adds 111 names, +47% over the plain page. Most are JavaScript libraries and frameworks (29), then analytics (12) and advertising (5), the client's category. 42% of the detections of the most common trackers (Google Analytics, Tag Manager, Facebook Pixel, Clarity...) were only visible in the browser, because other scripts inject them.

Other ways?
I tried two ways around the browser.

Reading tracker URLs out of the inline scripts in plain HTML costs nothing, but gives back only 11 of the 112 names only the browser finds. 
Downloading the script files and reading their text was worse than useless: of the 114 new detections I could check, only 25 were judged right (8 are not judged yet). Script text names far more than the page uses. Google's own tag code mentions Floodlight on every site that has it, lightbox libraries contain Facebook's `sharer.php`, consent managers list hundreds of vendors they block. 

## The three debate questions

### What were the main issues, and how would I tackle them?

**Sites that hide from crawlers.** 10 domains showed a bot check. Before, more showed, and I needed to go trough each domains, paste it myself in the browser to be sure that it checks. Sometimes it was the plain fetch that got blocked and the browser that got through, sometimes the opposite (1stlocksheathscouts.org.uk did both on different days). I don't try to get past these, I record them. Now: report which signals could not be checked. At scale: measure which tier gets blocked per site and use the one that works.

**Rules that match the wrong thing.** Most wrong detections were a rule matching something other than the product: a word inside another file name, a global variable that another library also uses, a URL in a security header, a vendor list inside a cookie banner script. I fixed them one at a time with reviewed corrections, each with the example that proves it. At scale I'd do it the same way but driven by data: watch for technologies that appear suspiciously often together with one platform, and review those first.

**Whose technology is it?** A site on Wix "uses" Cloudflare, Varnish and Amazon S3, but the owner never chose any of them. I needed clear definitions of what tehnologies to include and which not. I report a hosted platform's infrastructure apart. The list of hosted platforms is hand-written and grows when a review finds one I missed (DISH and Bandzoogle were added that way), which doesn't scale. Next step for scalling: detect "this is a hosted builder" from shared patterns across many sites instead of a list.

**Things the homepage doesn't show.** I only look at the homepage, before accepting cookies. Payment and chat tools often live on the cart or contact page, and some  sites may hold their trackers until you click Accept. For future: visit 2-3 subpages picked by platform (Shopify's `/cart`) or by keywords in the page's language, and record post-consent evidence as its own group.

**Missing rules.** 26 of the 29 technologies the blind inspection found and the tool missed have no rule at all (Wix apps, Psychology Today's badge, a local hosting company). No matcher fix helps there. The misses that did have a rule were each diagnosed and fixed with a reviewed rule, except a few: Cloudflare Turnstile was not a real miss (the inspection saw Cloudflare's bot check, the tool got the real page), and GoDaddy and Microsoft 365 need the DNS of a same-company redirect target, which is a collection change. 


### How would I scale this to millions of domains in 1-2 months?

A calculation with AI aproximately: 10 million domains in 45 days is about 2.6 domains per second, all day. The architecture I would keep: collection and detection are separate, so collection becomes stateless workers pulling domains from a queue and writing raw files to object storage, and detection becomes a batch job over those files that can be re-run whenever the rules change.

The expensive part is the browser, so I measured a tiered pipeline ([`docs/TIERING.md`](docs/TIERING.md)). Every domain gets the cheap tier: plain fetch, DNS, certificate, robots.txt. A domain goes to the browser only if the cheap tier found a shop, a tag manager, or a bot check that a browser can get past. On these 200 domains that renders 79, keeps 323 of the 349 names (93%; 349, not 347, because this comparison also reads the URLs inside inline scripts, which adds 2 names), and saves 34% of the time per domain. What it loses is recall on small plain sites (74% to 58% on the blind set, both with the fitted rules): libraries that only a running page shows. The rule for which blocked pages to send was read off these same domains, so I'd test it on fresh ones first.

Some numbers from my run: the plain fetch took about 4 seconds per domain and the browser about 10, and with 10 parallel fetches I did 200 domains in under 3 minutes. On paper that means a few machines for the cheap tier. The browser is a different matter: I only measured time, not CPU and memory, and those are what limit how many pages one machine can render at once. That's the first thing I'd benchmark.

Other things that matter at that size: politeness per host, not per domain (thousands of small sites share one hosting server), DNS caching, retries only for temporary errors (a timeout), resuming a crashed run with no starting over, deduplicating the input, and re-crawling by how often a site changes. For some domains, usign Common Crawl and HTTP Archive would be worth using to pre-filter dead domains and to compare results.

### How would I discover new technologies?

The browser records every request a page makes. A third-party host that no rule explains, seen on several unrelated sites, is a candidate for a new technology. So i  can rank those hosts by how many sites use them, name the owner with an open list like DuckDuckGo's Tracker Radar, and write a candidate rule.

The pipeline for the last step already exists: candidate rules go into `fingerprints/candidates/` with their evidence and example sites, and could later be accepted or rejected, and only accepted ones are promoted into the rules. An LLM can help suggest names for unknown hosts, but only as a suggestion in that queue, never straight into the results. At scale this runs continuously: every crawl produces a new ranked list of unexplained hosts.

## What I'd do next

In order of what I think pays most: subpages (cart, contact) chosen by platform(for scalling but also for the 200 domains I think it could be worth to try); a parked/hijacked-domain check; detecting hosted platforms from data instead of a list; the discovery loop above; a benchmark of browser CPU and memory to aproximate the time; and more labelled sites, because 20 is enough to find problems but small for exact percentages and analysis.

## Where to read more


- [`docs/METRICS.md`](docs/METRICS.md), [`docs/NAME_CHECK.md`](docs/NAME_CHECK.md): how precise it is, and every wrong detection
- [`docs/FAILURES.md`](docs/FAILURES.md): every domain the tool couldn't read, and why
- [`docs/DEFINITIONS.md`](docs/DEFINITIONS.md): what counts as a technology a site uses
