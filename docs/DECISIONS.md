# Decisions

Every non-trivial design choice gets an entry here, so the reasoning survives after the code changes.

Template:

```
## D-NNN <title>
- **Date:**
- **Context:** what problem forced a choice
- **Options considered:** the realistic alternatives
- **Decision:** what we chose
- **Why:** the reasons that decided it
- **Consequences:** what this makes easier, harder, or impossible
```

---

## D-001 Separate collection from detection
- **Date:** 2026-11-16
- **Context:** The tool has two very different jobs. Collection talks to the network: it is slow (seconds per site), can fail in many ways, and gives a slightly different answer each time it runs, because sites change and A/B-test. Detection matches fingerprint rules against what was collected: it is fast, deterministic, and is the part we will change most often while tuning rules and measuring accuracy.
- **Options considered:**
  1. One pass: fetch each site and detect immediately, keeping only the results.
  2. Two stages: `collect` saves raw artifacts per domain to disk (`data/raw/<domain>/`: HTML, headers, cookies, network requests, DNS, TLS); `detect` reads only those files, offline.
- **Decision:** Option 2. Network code lives only in the collection package; `detect` must run without network access, from `data/raw/` alone.
- **Why:**
  - Crawl once, iterate many times: a new or fixed rule is re-run over all 200 sites in seconds, without re-fetching.
  - Reproducible: the same raw files always give the same detections, so before/after comparisons in `EXPERIMENTS.md` measure the rule change, not the sites changing.
  - Every piece of evidence points back to a saved artifact that can be inspected.
  - It mirrors how large crawls work (for example, HTTP Archive stores crawl output and analyses it afterwards), so the design scales by changing storage, not structure.
- **Consequences:**
  - Disk space for raw artifacts (small for 200 sites; compression and object storage become a scaling topic).
  - Detection can only use what collection saved. A rule that needs data we never collected cannot be checked and must be reported as "not checked", never as "absent".
  - The artifact format becomes an interface between the two stages and must stay stable (changing it means re-crawling or migrating).

## D-002 Validate the input domain list, never modify it
- **Date:** 2026-11-16
- **Context:** The input is one Parquet column, `root_domain`, with 200 rows. Profiling it (`scripts/inspect_input.py`) found it already clean: lowercase, ASCII, no scheme, `www.`, path, port, whitespace, trailing dot or duplicates; the two international names are already in punycode (`xn--...`). Many loaders "normalise" input anyway (lowercase, strip `https://` and `www.`, cut paths).
- **Options considered:**
  1. Normalise every value, then use the result.
  2. Validate every value as it is; report the rows that fail (row number, value, reason) and continue with the valid ones.
- **Decision:** Option 2. A value must already be a valid lowercase ASCII hostname: at least two labels; each label 1-63 characters of `a-z 0-9 -`, not starting or ending with a hyphen; at most 253 characters in total; every `xn--` label must decode as punycode. Each domain keeps `original_input` (exactly as in the file) and gets a `display_name` (punycode decoded, e.g. `jobværkstedet.nu`) used only in human-readable output. Network and DNS always use the ASCII form.
- **Why:**
  - Normalising code for problems the file does not have is untested code that can only add bugs.
  - A silent fix hides an upstream data problem, and can merge two different rows into one or change which site we crawl (`www.x.com` and `x.com` can serve different sites).
  - Keeping the original value means every output row traces back to the exact input row.
  - Punycode is checked with Python's built-in `idna` codec (IDNA 2003) rather than the plain `punycode` codec: the plain codec accepts garbage such as `xn--a`. The stricter IDNA 2008 rules would need the `idna` package as a declared dependency, which this file does not need.
- **Consequences:**
  - A messier future input fails loudly instead of being crawled under a guessed name; a normalisation step would then be a deliberate, separate decision.
  - Invalid rows do not stop a run. They are listed on stderr, so they must be read, not assumed absent.
  - Platform subdomains in the list (e.g. `x.weebly.com`, `insieme.stanford.edu`) are valid hostnames and load like any other row; the column name `root_domain` is not taken as a guarantee.
  - Duplicate rows are not detected by the loader (the file has none).

## D-003 UTF-8 console output on Windows
- **Date:** 2026-11-16
- **Context:** On Windows, when Python's output goes to a pipe, a file or a non-console terminal, it is encoded with the legacy code page (here cp1250). That code page has no `ø` or `æ`, so printing the display name of `xn--jobvrkstedet-9cb.nu` (`jobværkstedet.nu`) crashed with `UnicodeEncodeError`.
- **Options considered:**
  1. Tell users to run Python in UTF-8 mode (`python -X utf8` or `PYTHONUTF8=1`).
  2. Print with `errors="replace"`, turning unknown characters into `?`.
  3. Have the CLI switch stdout and stderr to UTF-8 at start-up (`sys.stream.reconfigure(encoding="utf-8")`) when they use another encoding.
- **Decision:** Option 3, in `techdetect.console.use_utf8_output()`, called before every CLI command and by the inspection script.
- **Why:** Option 1 makes correctness depend on how the user starts the program. Option 2 loses information: `jobv?rkstedet.nu` is a different, wrong name. UTF-8 can encode every name.
- **Consequences:**
  - Redirected output (`> file.txt`, pipes) is UTF-8 bytes; a reader that assumes the legacy code page shows wrong characters, but nothing crashes and no data is lost.
  - A test runs the CLI as a subprocess with `PYTHONIOENCODING=ascii`, which reproduces the crash on any OS, so CI on Linux also guards this fix.

## D-004 User-Agent: an honest label for plain HTTP, a tagged Chrome for the browser
- **Date:** 2026-11-16
- **Context:** Every request tells the site who is asking, through the `User-Agent` header. Some sites answer robots differently (a challenge page, a block, a lighter page), so this choice changes what we can observe. It is also an ethics question: the crawler should be identifiable.
- **Options considered:**
  1. A normal Chrome User-Agent everywhere: sees what visitors see, but hides who we are.
  2. An honest robot label everywhere: transparent, but a real browser announcing itself as a robot is the most likely to be blocked.
  3. Honest label for the plain HTTP client; for the browser (a later collector), Chrome's normal User-Agent with our own tag appended.
  4. Option 3, plus an experiment re-running the plain HTTP step with a Chrome User-Agent to measure the difference.
- **Decision:** Options 3 + 4. The plain HTTP client sends `techdetect/<version> (+https://github.com/giulia223/WebTehnologiesScraping; research project)`: a name, a version and a page explaining the project. The browser will send Chrome's User-Agent with `techdetect/<version>` appended (and "HeadlessChrome" replaced by "Chrome"). Both values come from `config.py` and are recorded in every artifact's provenance header.
- **Why:**
  - This copies how the HTTP Archive crawls millions of sites every month. Its Web Almanac 2024 methodology (https://almanac.httparchive.org/en/2024/methodology, section "Tools > WebPageTest") lists the desktop crawler's User-Agent as "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 PTST/240613.172707": a real Chrome string with a `PTST/...` tag appended (the page lists the string without explaining the tag; it identifies WebPageTest, the tool that runs their crawl). Sites get a realistic browser that is still identifiable.
  - The plain HTTP client cannot run JavaScript, so pretending to be Chrome would be a disguise, not realism.
  - The experiment (hypothesis H8 in `EXPERIMENTS.md`) turns "do sites treat robots differently?" into a number instead of a guess.
- **Consequences:**
  - More challenge pages on the plain HTTP step. In the first 10-domain trial, 2 of 10 sites (one Cloudflare, one Akamai) answered with a challenge page; whether a Chrome User-Agent changes this is what H8 measures.
  - Results also depend on where the crawl runs: the same methodology page says HTTP Archive's "Test agents run from various Google Cloud Platform locations based in the USA", while ours runs from Romania (`crawl_location` in the provenance header). EU visitors get more consent banners, and some sites redirect by country.

## D-005 Raw artifact layout and atomic writes
- **Date:** 2026-11-16
- **Context:** Collection and detection are separate (D-001), so the files collectors write are the interface between the two. They must be easy to inspect, trustworthy as proof, and never half-written.
- **Options considered:**
  1. One JSON per domain with everything inside, HTML included as a string.
  2. One folder per domain (`data/raw/<domain>/`), one file pair per collection step: facts in JSON (`static.json`), the page in its own file (`static.html`).
  3. Standard web-archive files (WARC), compressed.
- **Decision:** Option 2.
  - HTML is never stored inside JSON. The JSON names its HTML file and stores its sha256 hash.
  - Every JSON starts with a `provenance` header: `collected_at` (UTC), `tool_version` (package version + git commit, read from the `.git` files), `crawl_location`, `user_agent`, `accept_language`.
  - Headers are stored as a list of `[name, value]` pairs, never a dict, so repeated headers (several `Set-Cookie` lines) all survive. Every redirect step is stored with its own status and headers.
  - Writes are atomic (`collect/storage.py`): a file is written to `<name>.tmp`, flushed to disk, then renamed with `os.replace`, which happens completely or not at all. HTML is written first, the JSON last, so a JSON on disk means its step finished.
  - No compression.
- **Why:**
  - An HTML file opens directly in a browser or editor; a page inside a JSON string is escaped and unreadable.
  - The sha256 lets anyone check that the page used as proof is the page that was fetched.
  - The provenance header explains differences between two crawls (other code, other country, other headers) without guessing.
  - Without atomic writes, a crash or Ctrl+C in the middle of a write leaves a truncated file that the next step would read as complete.
  - 200 domains take well under 1 GB, so compression would only make the files harder to inspect.
- **Consequences:**
  - Many small files (2 per step per domain): fine for 200 domains, a scaling topic for millions (compressed WARC files on object storage).
  - `tool_version` cannot tell whether the working tree had uncommitted changes when the crawl ran.
  - A leftover `.tmp` file after a crash is harmless but visible.

## D-006 Static fetch: success rule, fallbacks, retries and error categories
- **Date:** 2026-11-16
- **Context:** The plain HTTP fetch is the cheapest view of a site. A fetch can fail in many ways, and "failed" alone is useless for the final report: a dead domain, a bot wall and a slow server mean different things to a reader.
- **Options considered and decision** (each choice names the alternative it rejects):
  - **Addresses:** try `https://d`, `https://www.d`, `http://d`, and stop at the first success. Rejected: only `https://d`, which misses sites served only on `www.` or only over plain HTTP.
  - **Success:** final status 2xx, a non-empty body, and not a bot-challenge page. A parked page counts as success (it is a real answer about the domain). When every address fails, keep the most informative attempt: challenge page > an HTTP answer (4xx, 5xx, rate limited, empty) > no answer at all.
  - **Meta refresh:** follow `<meta http-equiv="refresh" content="...; url=...">` at most 2 times, since httpx does not. A refresh to the same URL, or without a URL, is not followed.
  - **Retries:** at most 2 per address, with exponential backoff and jitter, for timeouts and 5xx. A 429 waits for `Retry-After` (seconds or a date), capped at 60 s; a longer request gives up as `rate_limited`.
  - **TLS:** on a certificate error, fetch again without verification and record `tls_verified: false` plus the original error. Rejected: treating it as a failure, because the page is still the site's page, and a self-signed certificate is itself a finding.
  - **One pair of httpx clients per domain** (verified and unverified), sharing one SSL context. Rejected: one client for the whole run, because a client keeps a cookie jar and cookies from one site must not be sent to another.
  - **Limits:** the body is read up to 3 MiB (`html_truncated` records a cut); each request has an overall deadline equal to the configured timeout, on top of httpx's per-operation timeouts.
  - **Error categories:**

    | Category | Meaning |
    |---|---|
    | `dns_fail` | the name does not resolve: the domain is dead or misconfigured |
    | `timeout` | no complete answer in time, after retries |
    | `tls_error` | the secure connection could not be set up, even without certificate checks |
    | `conn_refused` | the server actively refused the connection |
    | `conn_error` | any other connection failure: reset, unreachable network, protocol error |
    | `redirect_loop` | too many redirects |
    | `http_4xx` | the server answered with a client error (also the rare final 3xx without a target) |
    | `http_5xx` | the server answered with a server error, after retries |
    | `rate_limited` | 429 that did not clear, or a `Retry-After` above 60 s |
    | `empty_response` | 2xx with an empty body |
    | `bot_challenge` | a Cloudflare, DataDome or Akamai challenge page answered instead of the site |
- **Why:** each category tells the reader of the final report something different: `dns_fail` is a dead lead, `bot_challenge` means "the site exists but we could not see it", `http_5xx` might work on another day. Challenge pages are kept on disk but never analysed, because their scripts belong to the anti-bot vendor, not to the site.
- **Consequences:**
  - Up to 3 addresses × 3 tries per domain in the worst case: polite, but slow for broken sites.
  - Platform subdomains also get a `www.` attempt (e.g. `www.x.booked.net`), which cannot work; harmless, but it adds an error to their record.
  - Challenge detection relies on a short list of vendor signals; an unknown vendor's wall is recorded as a normal page or an HTTP error.

## D-007 DNS probe: exact input host, fixed record set, one status per query
- **Date:** 2026-11-17
- **Context:** DNS records name services without loading any page: MX the email provider, NS the DNS host, TXT the SPF senders and site-verification tokens, a `www` CNAME often the CDN or hosting platform. The input list contains platform subdomains such as `x.weebly.com`, whose parent domain belongs to the platform, not to the site.
- **Options considered and decision:**
  - **Which name:** every query uses the input host exactly as given (`x.weebly.com`, never `weebly.com`). Rejected: querying a computed registered domain, because the parent's records describe the platform's own email and DNS, not this site's.
  - **Which records:** A, AAAA and CNAME for the host and for `www.`host; MX, NS, TXT and SOA for the host; TXT for `_dmarc.`host (the DMARC policy, which can name a mail-security vendor). The 11 queries of one domain run at the same time. SOA (the zone's primary name server and admin mailbox) was added after loading the rules showed 51 webappanalyzer `dns` rules matching it; for a subdomain without its own zone it records `no_answer`, since the exact host is queried.
  - **Resolver:** the machine's configured DNS servers (recorded in `dns.json`), 5 s total per query, and no search-domain suffix (otherwise a local suffix such as `.home` could be appended to a name).
  - **Status per query:** `ok`, `nxdomain` (the name does not exist), `no_answer` (the name exists, no record of that type), `timeout`, `no_nameservers` (every server failed, e.g. SERVFAIL), `error`. Rejected: one status for the whole domain, because "no MX" and "MX query timed out" mean different things.
  - **TXT text:** a TXT record split into 255-byte strings is joined back into one, so a long SPF record is stored as its owner wrote it.
- **Why:** each query result is an independent fact; recording failures per query lets the report say "not checked" instead of "absent" when a query timed out.
- **Consequences:**
  - Answers depend on the resolver used (some sites answer differently by location); the nameservers are recorded for that reason.
  - Eleven queries per domain: 2,200 small queries for 200 domains, bounded by the domain concurrency.
  - A rule update that uses a record type the probe does not ask for would silently never match; the record types used by the rules should be checked against this list.

## D-008 TLS probe: final host, chain verified but name checked separately
- **Date:** 2026-11-17
- **Context:** The certificate's issuer can name the certificate authority or the platform. A server can also answer with a certificate for other names (e.g. the hosting provider's default certificate), which describes the provider's machine, not the site. Python's `ssl` module only parses a certificate that passed verification; for any other it gives the raw bytes.
- **Options considered and decision:**
  - **Which host:** the host of the static fetch's final URL (after redirects), since that server actually served the page. If the static fetch failed or has not run, the input host. `probed_host_source` records which.
  - **Name check:** the connection verifies the certificate chain but not the name (`check_hostname=False`), and `hostname_match` is computed separately from the certificate's subjectAltName DNS names (a `*` stands for exactly one leftmost label; the subject CN is not used, as in current browsers). Rejected: letting Python check the name, because a mismatch would abort the handshake and the certificate could not be recorded at all.
  - **Mismatching certificate:** kept in `tls.json` with `hostname_match: false`, and not used for detection.
  - **Unverified certificate** (self-signed, expired, unknown authority): connect again without verification, store the certificate as PEM plus its sha256, `chain_verified: false` and the verification error. Issuer, subject and names stay null and `hostname_match` is null (unknown). Rejected for now: adding the `cryptography` library to parse every certificate, a new dependency for what may be a few sites; and Python's private `ssl._ssl._test_decode_cert`, which is internal test code. Because the PEM is kept, switching later is a re-parse of saved files, not a re-crawl.
  - **Trusted authorities:** the same set as the static fetch (httpx's), so "verified" means the same thing in `static.json` and `tls.json`.
- **Why:** the certificate is evidence only if it belongs to the site; recording the match instead of failing keeps every observation while letting detection filter.
- **Consequences:**
  - Unverified certificates give no issuer to detection until a parser is added; their number in the full crawl decides whether that is worth a dependency.
  - If the final URL is on another site (a redirect to a different domain), the probe describes that site's certificate; `probed_host` makes this visible.
  - A site served only over plain HTTP gets a failed probe on port 443, recorded with its error category.

## D-009 robots.txt: final origin, kept only if it looks like plain text
- **Date:** 2026-11-17
- **Context:** robots.txt often names the platform through its rules (`/wp-admin/`, `/checkout`). Many servers answer an unknown path with their normal HTML page and status 200 (a "soft 404"); treating that page as robots.txt would put the page's markup where rules are expected. Other well-known files (favicon, sitemap.xml, security.txt, ads.txt) are deliberately not fetched: ads.txt lists who may sell ads on a site, not what runs on it.
- **Options considered and decision:**
  - **Where:** the origin of the static fetch's final URL plus `/robots.txt`, so it belongs to the site that served the page; `https://<input host>/robots.txt` if the static fetch failed. Redirects are followed; a bad certificate is handled as in the static fetch (fetched anyway, recorded).
  - **Keep rule:** keep the body only if the status is 200 and the body is not empty, has no NUL bytes, and does not start with `<` after whitespace and a UTF-8 byte-order mark (a real robots.txt starts with a rule, a `#` comment or a blank line). Otherwise `rejected` with a reason: `status_not_200`, `empty`, `binary`, `html_soft_404`. Rejected: deciding by Content-Type, because some servers send a valid robots.txt as `text/html` and soft-404 pages come with any type; the Content-Type is still recorded.
  - **Storage:** a kept body is saved as `robots.txt` next to `wellknown.json`, which names it and stores its sha256, like `static.html`. A rejected body is not saved (only its size and sha256), and a `robots.txt` from an older run is deleted.
  - **Size:** read up to 500 KiB, the minimum robots.txt parsers must handle (RFC 9309); a cut is recorded.
- **Why:** a robots.txt that is really an HTML page would make detection see the homepage's markup twice, once mislabelled; rejecting it keeps each artifact what it claims to be.
- **Consequences:**
  - A plain-text error page ("Not found" as text/plain with status 200) would be kept; rules that read robots.txt must match real directives, not any text.
  - One request per domain, no retries: a temporary failure leaves robots.txt unchecked for that run.

## D-010 Browser page load: independent start, settle steps, language set at launch
- **Date:** 2026-11-17
- **Context:** Many technologies only appear after JavaScript runs (tag managers, chat widgets, single-page apps). A headless Chromium (driven by Playwright) shows what a visitor's browser builds, at a much higher cost per site than a plain HTTP request.
- **Options considered and decision:**
  - **Start:** the browser starts from the input domain itself with the same addresses and success rule as the static fetch (`https://d`, `https://www.d`, `http://d`; 2xx, non-empty page, not a challenge), sharing the same code. Rejected: starting from the static fetch's final URL, because a difference between the two final URLs is itself evidence (a JavaScript redirect) and would be hidden. `browser.json` keeps `start_url`, `final_url` and a `final_url_differs_from_static` field, filled in by the full collect command once both steps have run.
  - **Letting the page settle:** wait for the DOM, then for "network idle" (no requests for 500 ms) up to 8 s without failing, scroll one screen every 250 ms to the bottom (at most 30 screens, so endless-scroll pages stop), scroll back up, wait 2 s. Lazy-loaded widgets and tags fire on scroll or after load; without these steps they would be missed.
  - **Final page:** the status is that of the last main-frame document, so a JavaScript redirect after load is followed and recorded; `final_url` is the address on screen at the end.
  - **Saved:** `rendered.html` (the DOM after JavaScript, with sha256), `screenshot.jpg` (viewport only, JPEG quality 40, for a human check), and in `browser.json`: cookies, every external script URL, inline scripts (50 KB each, 200 KB in total; cuts and dropped scripts are counted), meta tags, iframe URLs (including iframes added by JavaScript), the main document's header pairs.
  - **Errors:** Chromium's network error codes are mapped to the static fetch's categories; a new category `browser_error` covers failures that are not the network (a download instead of a page, a crashed tab). A certificate error is retried once with certificate checks off and recorded (`tls_verified: false`), as in the static fetch. No other retries: a browser attempt costs seconds. A challenge page is recorded and left alone: no headed retry, no attempt to pass it.
  - **Concurrency:** 4 pages at a time by default (10 for HTTP steps), because each page costs far more memory and CPU.
  - **User-Agent:** Chromium's own string with "HeadlessChrome" replaced by "Chrome" and `techdetect/<version>` appended (see D-004).
  - **Language:** Chromium is launched with `--lang` and `--accept-lang` derived from the configured `Accept-Language`, and every context sends that header. Rejected: Playwright's `locale` option, which the plan first used. Tested against a local server, with a User-Agent override it sent `en-US` on page, iframe and worker-script loads but `en-US,en;q=0.9` on other requests, which no real browser does. With the launch flags, every request carries `en-US,en;q=0.9` and `navigator.languages` is `["en-US", "en"]`, like an English Chrome. The value actually sent on the main document is what the provenance header records.
- **Why:** each step either shows something a visitor's browser would load or keeps the result comparable with the static fetch.
- **Consequences:**
  - About 10 s per site that loads; slower for sites that never go idle.
  - Bot protection treats the two steps differently. In a 5-site trial, disneystore.com answered the plain HTTP client normally but showed the browser Akamai's "Access Denied", while 231selfstorage.com showed a Cloudflare challenge to the HTTP client and its real page to the browser. Neither step alone sees every site.
  - The screenshot is the top of the page only.

## D-011 Browser network capture: context-level listening, record then block, origin by frame
- **Date:** 2026-11-17
- **Context:** Many technologies are visible only as requests the page makes: analytics and ad pixels, tag managers, chat widgets, font services. On Shopify stores, apps' tracking pixels run in a sandbox: custom pixels in an iframe ("lax" sandbox), app pixels in a Web Worker ("strict" sandbox). A capture that listens only to the page would miss part of this.
- **Options considered and decision:**
  - **Where to listen:** at browser-context level (every page, iframe and worker of the context), not on the page alone. Each request records URL, method, resource type, status, failure, `blocked`, and its origin.
  - **Origin:** `main`, `iframe` (with the frame's URL), `service_worker`, or `other_page` (a popup). Rejected: talking to Chromium's debugging protocol directly to attach to each worker, which would label worker requests exactly but needs a fragile workaround (Python Playwright cannot address a worker's own protocol session). Accepted limit: Playwright reports a dedicated Web Worker's requests with the frame that created the worker, so they appear as `main` (or as that iframe). The workers seen are listed in `workers`, so the limit is visible in the data.
  - **Record, then block:** images, fonts and media are recorded with `blocked: true`, then aborted. The URL is the evidence (`facebook.com/tr?id=...`, Google Fonts); the bytes are not needed. Rejected: letting them load, which costs time and bandwidth for no extra evidence. Accepted cost: a blocked request's response (e.g. a cookie a pixel would set) is never seen, and screenshots show no images.
  - **Headers:** response header pairs are kept for documents and scripts (servers, CDNs and frameworks announce themselves there), not for every request, to keep `browser.json` readable.
  - **File contents:** for external scripts and stylesheets only the URL is kept, not the file. Rules that need file contents are reported as not checked.
- **Why:** for a lead-generation client, the tools a company pays for (analytics, ads, marketing, chat) mostly show up as network requests, and many only after JavaScript runs.
- **Consequences:**
  - `browser.json` grows to a few hundred KB on busy pages (about 400 KB and 350-500 requests on the two Shopify stores of the trial).
  - On those two stores, custom-pixel requests (e.g. Google Analytics) were captured with their sandbox iframe as origin. App-pixel requests (e.g. TikTok's `analytics.tiktok.com/api/v2/shopify_pixel`) were captured but labelled `main`, with the pixel workers listed in `workers`. The request URLs are there either way; only the origin label is imprecise for workers.

## D-012 Fingerprint rules: webappanalyzer at a pinned commit, parsed the way Wappalyzer parsed them
- **Date:** 2026-11-17
- **Context:** Detection needs a large, maintained set of rules ("this header / script URL / cookie means this technology"). Wappalyzer's rule set was the open standard until its repository went private in 2023; `enthec/webappanalyzer` continues it in the same JSON format (7,704 technologies and 15,838 patterns at the pinned commit). Rules change every week upstream, and a result is only reproducible if we know exactly which rules produced it.
- **Options considered and decision:**
  - **Source and pinning:** copy webappanalyzer's `technologies/*.json`, `categories.json`, `groups.json`, `schema.json` and its LICENSE (GPL-3.0) into `fingerprints/webappanalyzer/`, byte for byte, at one commit (`9e52f10a9533c8f7f8d0703c843f7295d56c95a3`, 2026-11-15). `fingerprints/VERSION` records the source, commit, commit date and copy date. `scripts/update_fingerprints.py <sha>` repeats the copy for another commit (the 80 MB of logos are not copied). Rejected: downloading the rules at run time, because two runs could then use different rules without saying so.
  - **Rule-set version:** `webappanalyzer@<commit>+custom:<hash>` (plus `+candidates:<hash>` when candidates are loaded). The hash is computed on the rules' parsed content, not on file bytes, so line endings (CRLF on Windows, LF on Linux) and key order do not change it. Collected browser data will store this version, so a later rule change is visible.
  - **Parsing:** follow Wappalyzer's own code, read in the last published npm package (`wappalyzer@6.10.66`, `wappalyzer.js`), so every rule means what its author meant:
    - `transformPatterns(patterns, caseSensitive = false, isRegex = true)`: a string or array becomes a list (`patterns = { main: patterns }`), and dictionary keys are lowercased unless `caseSensitive` (`parsed[caseSensitive ? key : key.toLowerCase()]`). It is called with `caseSensitive` true only for `js` (and `dom` selectors); headers, cookies, meta and dns names are lowercased.
    - A `dom` list is expanded to `{ [selector]: { exists: '' } }`: the element exists.
    - `parsePattern`: `.split('\\;')`, then each tag `attr.split(':')` with `attrs[attr.shift()] = attr.join(':')` (split on the first colon, so `version:\1?a:b` stays whole); `confidence: parseInt(confidence || 100, 10)`, `version: version || ''`; regexes are case-insensitive (`new RegExp(..., 'i')`).
    - `implies` entries keep their own `confidence` and `version` tags; `requires`, `excludes` keep names; `requiresCategory` keeps category ids.
  - **Regex dialect:** rules are written for JavaScript's `RegExp`; they are compiled with Python's `regex` module (lookbehind, named groups), case-insensitive. A pattern it rejects is skipped and listed with the reason in the load report, never rewritten by hand. At this commit all 15,838 patterns compile.
  - **Quantifiers:** Wappalyzer rewrote `+` to `{1,250}` and `*` to `{0,250}` to limit slow matching on long text. We keep patterns exactly as written and rely on a time limit per match in the matcher instead, so a match never silently stops at 250 characters.
  - **Our own rules, same schema:** `fingerprints/custom/*.json` always loads; `fingerprints/candidates/*.json` only on request (`--include-candidates`) and only entries marked `proposed` or `accepted`, never `rejected` or unmarked (both counted). A custom or candidate entry with the name of a known technology adds its patterns and categories to it (listed in the report); it never replaces the upstream rule.
  - **Load report** (`techdetect rules`): technologies, patterns per field, **patterns per confidence value**, skipped patterns with reasons, unknown tags, fields not used (e.g. `probe`, which needs active requests we do not make), merges, candidates left out, relations or category ids that point nowhere.
- **Why:** the rules are the largest input to every number in the report; pinning them and parsing them exactly like their reference implementation makes the numbers reproducible and comparable with Wappalyzer-style tools.
- **Consequences:**
  - Updating the rules is a deliberate step (new commit, new EXPERIMENTS row), not a side effect.
  - JavaScript and Python regexes differ in small ways (e.g. `\w` and `\d` also match non-ASCII letters and digits in Python); a pattern can match slightly more text than in a browser.
  - Some fields need data we do not collect (script and stylesheet contents, `probe`); the matcher must report such rules as not checked, not as absent.
  - Loading takes about 1 s (all regexes are compiled up front).
