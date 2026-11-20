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

## D-013 JavaScript globals: ask every rule's chain while the page is open, record what was asked
- **Date:** 2026-11-17
- **Context:** Many rules look at JavaScript variables (`jQuery.fn.jquery` gives jQuery's version, `window.Shopify` means Shopify). These exist only in the browser's memory while the page is open; detection runs offline later and can never ask the page again.
- **Options considered and decision:**
  - **What is asked:** every `js` chain of the loaded rules (webappanalyzer + custom; 5,645 chains at the pinned rule set), in one call per page after it settled. Rejected: a general snapshot of all variable values, which would be large and mostly noise.
  - **How a chain is read:** exactly as Wappalyzer's own driver did (`getJs` in `driver.js`, npm `wappalyzer@6.10.66`): `chain.replace(/\[([^\]]+)\]/g, '.$1')`; a first part that is a plain name is read as a global through `new Function("return typeof x === 'undefined' ? undefined : x")` (this also finds `let`/`const` globals, which are not properties of window), otherwise the walk starts at `window`; each step requires `value instanceof Object && Object.prototype.hasOwnProperty.call(value, method)`, so inherited properties and their getters are never read; a falsy root counts as absent; the value kept is `typeof value === 'string' || typeof value === 'number' ? value : !!value`.
  - **Added to Wappalyzer's version:** each chain in its own try/catch (a getter that throws is recorded in `js_chain_errors` and does not hide the other chains or stop the crawl); when the page's Content-Security-Policy forbids building code from a string (`EvalError`), the global is read as `window[name]` instead; strings capped at 1,000 characters; non-finite numbers stored as text (JSON has no NaN).
  - **Rule-set record:** `browser.json` stores `ruleset_version` and `js_chains_asked`, the exact list asked. Detection treats a `js` rule whose chain is not in that list as not checked, never as absent: a rule added after the crawl needs a browser re-run, while an `html` rule can be re-matched offline on the saved page. If reading fails as a whole, `js_chains_asked` is empty and `js_globals_error` says why.
  - **Variable names:** `window_keys` lists the page's own enumerable variables (names only, no values), for later analysis. The baseline subtracted is taken in the same page, by an init script that runs before the page's scripts and stores `Object.keys(window)` in a non-enumerable property. Rejected: the planned baseline from an `about:blank` page; tested on three sites, it left browser names such as `cookieStore` and `caches` in every https site's list, because `about:blank` is not a secure context and lacks them. Frame indexes (`"0"`, `"1"`, one per iframe) are left out too.
- **Why:** the browser step is the only chance to read these values; asking every chain the rules know, and saying exactly which ones were asked, keeps "not found" and "not checked" apart.
- **Consequences:**
  - `browser.json` carries the list of asked chains (about 84 KB) on every domain; a rule-set version alone would be smaller, but the list makes each file self-explanatory.
  - The init script is a small footprint a site could notice (a non-enumerable property on window).

## D-014 The collect run: no resume, a completion marker, and counts read back from disk
- **Date:** 2026-11-17
- **Context:** A full collection (200 domains, five steps) takes minutes and can be interrupted (Ctrl+C, crash, network loss). The dataset it produces is the input of every later number, so a half-finished or half-overwritten folder must never pass for a finished one.
- **Options considered and decision:**
  - **Interrupted runs:** no resume. An interrupted run is run again from the start. Rejected: resuming from the run log, which needs rules about partially written domains, changed inputs and changed code versions; at this size a re-run costs minutes. Resume is a scaling topic.
  - **Three safety pieces instead:** every file is written atomically (a JSON on disk means its step finished, see D-005); `_run_complete.json` is written only at the very end, so a folder without it is an unfinished run; and a folder that already holds a finished run is never collected into again unless `--overwrite` is given (or another `--out` is chosen), so a crashed re-run cannot destroy a good dataset. With `--overwrite`, the marker is removed at the start, so the folder counts as unfinished until the re-run completes.
  - **Step order:** each step runs over all domains before the next, in a fixed order: static, dns, tls, wellknown, browser. The TLS and robots.txt steps read `static.json` to know the site's final address, so static always runs first, whatever order `--only` lists.
  - **Re-running one step:** `--only browser --overwrite` replaces only the browser files and keeps the other steps' files. `_run_complete.json` lists every step with when it ran and with which code version, and keeps a short record of the earlier runs, so mixed datasets (e.g. browser re-run after new `js` rules) are visible.
  - **Run log:** `_run_log.jsonl`, one line per (domain, step) with status, duration and error category, appended as each domain finishes, so an interrupted run still shows how far it got. Lines carry `run_started_at`, so several runs can share the file.
  - **Counts:** the summary table and the counts in `_run_complete.json` are read back from the files on disk, not from memory: they are exactly what detection will find. A domain whose step crashed has its old file for that step deleted, so it counts as `missing` instead of passing an older run's file off as this run's.
  - **Timing:** each domain's duration is measured from the moment it gets a concurrency slot, so the average time per domain per step is the work itself, not the wait.
  - **Final URL comparison:** after the static and browser steps, `browser.json` gets `final_url_differs_from_static`: true when both reached a real page but ended at different addresses (usually a JavaScript redirect), null when either did not reach a page.
- **Why:** the folder's state must always say the truth about itself: finished or not, which code and rules produced which files, and what really is on disk.
- **Consequences:**
  - A re-run after an interruption repeats work already done (minutes at this size).
  - The tool version is the last commit; it cannot tell whether the working tree had uncommitted changes when the run happened.
  - In a 10-domain trial the average time per domain was about 1.5 s static, 1.8 s DNS, 0.8 s TLS, 1.1 s robots.txt and 5.9 s browser (38 s in total with concurrency 10 for HTTP steps and 4 for the browser). These are trial numbers, not the full-run measurement.

## D-015 Matcher: what each rule field is matched against, and what counts as the site
- **Date:** 2026-11-19
- **Context:** The rules were written for Wappalyzer's engine, which matched each field against a specific piece of page data. Our data comes from saved artifacts (two pages, headers, cookies, requests, DNS, certificate, robots.txt), and some of it belongs to third parties (a CDN, a widget) rather than to the site.
- **Options considered and decision:**
  - **Field to data,** following Wappalyzer's `analyze()` (wappalyzer.js) and its driver (driver.js), npm `wappalyzer@6.10.66`:

    | Field | Matched against | From |
    |---|---|---|
    | `html` | the whole page | `static.html`, `rendered.html` (each separately) |
    | `text` | the page's visible text | both pages |
    | `url` | the page's final URL | both pages |
    | `scriptSrc` | each `<script src>`, made absolute | both pages |
    | `meta` | `<meta name/property>` -> content | both pages |
    | `dom` | elements by CSS selector: present / text / attribute | both pages (selectolax, offline) |
    | `headers` | header name -> value, same site only | static redirect steps and final page; browser main document and document/script responses |
    | `cookies` | cookie name -> value, same site only | static and browser cookies |
    | `js` | JS chain -> value (true/false written as JavaScript writes them) | `browser.json` |
    | `xhr` | the host name of each xhr / fetch request | `browser.json` requests |
    | `dns` | record type -> records (CNAME of host and www) | `dns.json` |
    | `certIssuer` | issuer organisation and common name | `tls.json`, only if the certificate covers the host |
    | `robots` | the file's text | `robots.txt` |

    `xhr` takes fetch requests too: Wappalyzer's driver listened for XHR only, but Chrome's extension API, which its browser extension used, reports `fetch()` as XHR, and modern trackers use `fetch`.
  - **Same site (headers, cookies):** only responses and cookies on the site's registered domain(s): the input's, plus a final URL's when the site redirected to another registered domain (registered domains from tldextract's bundled Public Suffix List, offline). So `cf-ray` on `static.<site>` counts, but `cf-ray` on `cdnjs.cloudflare.com` or a widget's `PHPSESSID` does not. Rejected: the main page only (Wappalyzer's driver), which misses the site's own asset hosts; it remains a one-line switch if precision measurements show false positives. `scriptSrc` and `xhr` still match third-party URLs: naming third parties is their purpose.
  - **Which pages are matched:** only a page whose step reached a real page (`status: ok`). A challenge page belongs to the bot-protection vendor, and an error page may belong to a host or parking service, so neither is matched. DNS, TLS and robots.txt are matched whatever happened to the pages.
  - **Tier:** `declared` for `meta`, `robots` and DNS TXT records (the site states something: a generator tag, a verification token); `observed` for everything else, including DNS MX/NS/CNAME/SOA, which show a service in use.
  - **Not checkable:** `scripts` and `css` (file contents are not saved), `dom` properties (exist only in a live browser), `js` chains the browser was not asked about, and `js`/`xhr` when no browser page was loaded. They are counted per domain as not checked, never treated as absent.
  - **Evidence:** technology, field, key, artifact, the rule text, confidence, version template, regex groups, matched value (up to 200 characters), context (40 characters each side), location (the URL or host), tier. For presence rules (an empty pattern: "this header exists") the value itself is shown, and for a `dom` element the element's markup, so every record shows its proof.
  - **Slow patterns:** every search has a 0.5 s time limit (the `regex` module's `timeout`); a timeout is recorded and the other patterns still run. This replaces Wappalyzer's rewrite of `+` / `*` into `{1,250}` (see D-012). Measured cost of the limit: about 25 % more time on `html` patterns.
  - **One wildcard:** Google Analytics' cookie rule `_ga_*` (GA4 cookies are named `_ga_<id>`) is read as a wildcard; it is the only rule name with `*`.
  - **JavaScript escapes (loader):** a JavaScript regex `\A` or `\a` means the letter A; in Python they mean "start of text" and "bell". The loader rewrites such escapes to the plain letter (4 patterns at the pinned rule set: `\APC\b`, `\Asendia\b`, `openpay\.com.\au`, `\api\.sizemeai\.com`) and lists them in the load report.
- **Why:** each rule then meets the data its author wrote it for, and the same-site rule keeps a site from being credited with its suppliers' servers.
- **Consequences:**
  - Speed on one process, measured on the 200 collected domains: median 1 s per domain, 2-6 s for pages near 1 MB, about 5 minutes in total (all 15,838 patterns, two pages each; no search hit the time limit). Joining `scriptSrc` patterns into alternations to cut the number of searches was tried and measured slower (4.2 s against 1.6 s on 379 script URLs), because the `regex` module tries every alternative at every position; it was removed. Domains are independent, so `detect` can match them in parallel processes.
  - A host whose suffix is not on the Public Suffix List (e.g. `.example` in tests) is its own registered domain, so its subdomains do not count as the same site.
  - A `cdnjs` match will later imply Cloudflare (a webappanalyzer `implies` rule), as an inferred technology, even though no Cloudflare evidence is matched here.

## D-016 Confidence formula: one pattern counts once, sum capped at 100
- **Date:** 2026-11-19
- **Context:** A technology is usually found by several clues. Each rule pattern carries a confidence: the rule author's estimate of how strong that clue is (untagged patterns = 100, `\;confidence:50` = 50). **Confidence is not a measured probability of being right; measured accuracy is precision, against hand-checked labels.** The clues of one technology must be combined into one number.
- **Options considered:**
  1. **Wappalyzer's sum**, verified in its last published code (npm `wappalyzer@6.10.66`, `wappalyzer.js`, `resolve()`): for every detection of a technology, `confidence = Math.min(100, confidence + pattern.confidence)`. Every *match* adds, so one weak pattern found on two pages, or in eight script URLs, reaches 100.
  2. **The same sum, but each pattern counted once:** matches are grouped by (technology, field, name, pattern); each group adds its confidence once, however many times and in however many artifacts it matched. Different patterns still add up; the total is capped at 100.
  3. **Noisy-OR:** `1 - (1 - c1) x (1 - c2) x ...` with confidences as fractions, from the "noisy-OR gate" in Bayesian networks (Judea Pearl, *Probabilistic Reasoning in Intelligent Systems*, 1988); used for this same challenge by stamatierobert9/Veridion-Project. Two hints at 50 give 75 instead of 100. It assumes the clues are independent.
- **Decision:** option 2, in one small function (`combine_confidence`) so noisy-OR can be swapped in and measured later.
- **Why:**
  - The same pattern seen again is the same clue, not new evidence: a 50-confidence script name in 8 script tags on two pages is still one hint (50, with `match_count: 16`), while two different 50 patterns are two hints (100).
  - Apart from that, it is Wappalyzer's formula, so results stay comparable with Wappalyzer-style tools.
  - Weakness: two weak hints become "certain" (50 + 50 = 100). Noisy-OR would say 75; whether that changes precision is measured, not assumed.
- **Versions** (same function family, from Wappalyzer's `resolveVersion()` and `resolve()`): group N of the match replaces `\N` in the template; `\N?a:b` gives `a` if group N matched something, else `b`; a group longer than 10 characters is not used; leftover references are removed. A version is kept only if it is non-empty, at most 15 characters and not a timestamp-like number (Wappalyzer: "Ignore long numeric strings like timestamps"). Unlike Wappalyzer, which kept only the longest version, every distinct version is kept (`1.12.4` and `3.6.0` when both are on the page).
- **Consequences:**
  - Each technology result lists its pattern groups with `match_count`, the artifacts they matched in, and up to 3 example matches, preferring different places.
  - On 60 of the collected domains (900 technology results: 890 at 100, 2 at 50, 8 at 0), counting each pattern once changed no confidence compared with Wappalyzer's every-match sum: almost every pattern is already worth 100. The rule matters only for weak patterns, which are rare in the rule set (about 200 of 15,838).
  - Rule authors use `confidence:0` for hints they do not trust alone; such a technology stays at 0 unless another clue adds to it, and is still reported (the confidence threshold defaults to 0).

## D-017 Relations between technologies: Wappalyzer's implies, excludes and requires, in its order
- **Date:** 2026-11-19
- **Context:** Rules relate technologies: WordPress `implies` PHP and MySQL; Angular `excludes` AngularJS; WooCommerce `requires` WordPress; some rules require a category ("any ecommerce platform"). The rules were written for Wappalyzer's engine, so their meaning is its behaviour. At the pinned rule set 1,025 technologies use `implies`, 700 `requires`, 90 `requiresCategory`, 47 `excludes`.
- **Decision:** copy Wappalyzer's behaviour and order, read in its last published code (npm `wappalyzer@6.10.66`), with no extra layers:
  - **Order:** `resolve()` combines each technology's matches, then calls `Wappalyzer.resolveExcludes(resolved)`, then `Wappalyzer.resolveImplies(resolved)`. Excludes run first, so an implied technology is never excluded.
  - **Excludes** (`resolveExcludes`): for each technology in the result, every technology named in its `excludes` is removed (`resolved.splice(index, 1)`). We keep a record of what was removed and by which technology.
  - **Implies** (`resolveImplies`): `do { ... } while (resolved.length && !done)`, repeated until a pass adds nothing. An implied technology is added only `if (resolved.findIndex(...) === -1)`, i.e. if it is not already in the result: a direct detection is never changed, a technology is never added twice, and A <-> B loops end. It gets `confidence: Math.min(confidence, _confidence)` (the implying technology's confidence and the implies entry's `\;confidence:` tag, default 100) and `version: version || ''` (the entry's `\;version:` tag). A chain such as A -> B -> C passes the lower confidence along. Here it is marked `tier: inferred` with `implied_by` naming the technology that added it.
  - **Requires** (`setTechnologies` and the driver): a technology with `requires` or `requiresCategory` is taken out of the normal analysis and analysed only once one of the required technologies, or a technology of a required category, is in the resolved result (implied ones included); then everything is resolved again. One requirement is enough: a technology requiring `["WordPress", "Elementor"]` is registered under each name and analysed when either is found. Here all technologies are matched in one pass, and the same rule is applied afterwards: a technology whose requirement is not met is dropped and listed with its requirements; one whose requirement is met joins, and excludes/implies run again.
  - **Differences, both deliberate and small:** Wappalyzer throws an error on a relation naming an unknown technology; here the load report lists them (none at the pinned rule set) and they are ignored. Wappalyzer removes items from the array it is iterating over, so which of two mutually exclusive technologies survives can depend on array positions; here technologies are processed in a fixed order and one already removed excludes nothing.
- **Why:** the relations are part of the rules' meaning; a different order (e.g. implies before excludes) would remove technologies their authors meant to keep.
- **Consequences:**
  - The roadmap expected "WooCommerce gives WordPress, PHP and MySQL as inferred". In the pinned rules WooCommerce *requires* WordPress instead of implying it, so WooCommerce counts only when WordPress was found directly, and WordPress then implies PHP and MySQL. A WooCommerce marker on a page with no sign of WordPress is dropped and listed.
  - "Any one requirement" can keep a plugin when only one of its two platforms was found; that is how the rules were used by Wappalyzer.
  - Inferred technologies are reported separately from the core count, never mixed in.
  - On the 200 collected domains (match, combine, resolve): 426 distinct technologies in the final results, 414 found directly on at least one domain and 12 only ever inferred. The most frequent implications are WordPress -> MySQL (71 domains) and WordPress -> PHP (54; PHP was seen directly on the others). 19 matches were dropped for an unmet requirement (e.g. Element UI 7 times) and 24 were excluded, all Underscore.js excluded by Lodash: both use the global `_`, so their clues overlap, and the Lodash rule says the pair is Lodash.

## D-018 `detect`: domain status, what is counted, and the output files
- **Date:** 2026-11-19
- **Context:** The headline question is "how many different technologies did you find on the 200 domains?" (Veridion reported 477, without saying how they counted). A count is only meaningful with its rules: what counts as a technology, which domains count, and what is kept apart. Failed domains must never look like sites with no technologies.
- **Options considered and decision:**
  - **One line per input domain** in `detections.jsonl`, failed ones too, in input order, with every detection's evidence (pattern groups with up to 3 examples: artifact, location, matched value, context), what was excluded, what was dropped for an unmet requirement, which artifacts were used, and what could not be checked.
  - **Domain status:** `ok` if the static or the browser fetch reached a real page; otherwise `bot_challenge` or `failed:<category>` (`dns_error`, `timeout`, `tls_error`, `connection_error`, `http_error`, `rate_limited`, or `not_collected` when no step ran). With two failed fetches the most informative reason wins: challenge page > HTTP answer > no answer. A domain with status `failed:*` and no detections is "site failed", never "0 technologies".
  - **Redirects:** `redirected_to` is the final page's registered domain when it differs from the input's (`www.` and http -> https do not count); the browser's final URL is preferred, since it also follows JavaScript redirects. Such a domain keeps its detections in `detections.jsonl` but is left out of every count and average, and listed in `summary.json` as `input -> target`: its technologies belong to another site.
  - **Name-level counting:** a technology counts once however many domains use it, without versions.
  - **Core and the separate groups:** core = technologies with observed or declared evidence from the website itself (page, headers, cookies, requests, JS, its certificate, its robots.txt), **plugins and apps included**. Reported apart, never mixed in: technologies only ever inferred through `implies`; technologies only ever seen in DNS (a technology with DNS and website evidence stays core); and **themes**.
  - **Plugins count, themes do not (changed after Run 1's first version):** a plugin or app is software that adds a capability (forms, SEO, cookie consent, a shop), and for this tool's purpose they matter most: the task's client sells an analytics plugin for Shopify stores and is looking for sites using competing products, i.e. other apps and plugins. Technology-profiling tools such as Wappalyzer list plugins as technologies, and the rule set has them as ordinary entries. A theme is mostly the site's look, its evidence is usually a stylesheet path, and it tells a salesperson little ("Twenty Twenty-Four" is WordPress's default), so themes are reported as their own group. A technology is a theme here only if it has a theme category (WordPress, Shopify or Drupal themes) and nothing but theme or plugin categories: Divi and Bricks are also "Page builders" and stay core. The first version kept "WordPress plugins/themes and Shopify apps" all apart (core 329); counting plugins gives core 347.
  - **Averages and "0 technologies"** are computed over `ok` domains that were not redirected, and the denominator is written next to them ("over 156 ok domains of 200").
  - **Confidence threshold:** `--min-confidence`, default 0 = count everything, like Wappalyzer; every detection shows its confidence. The threshold applies to the counts and the tables; `detections.jsonl` always keeps every detection.
  - **Not checked:** `summary.json` lists, per field, the patterns that could not be checked and on how many domains (script and stylesheet contents, DOM properties, JS chains not asked, JS/XHR on domains without a browser page), never as "absent".
  - **Unfinished collections:** `detect` refuses a raw folder without `_run_complete.json` unless `--allow-partial` is given, and `summary.json` then says `partial_run: true`.
  - **Speed:** domains are matched in parallel processes (one per CPU core minus one); each loads the rules once. 200 domains take about 15 s with 19 processes, against about 5 minutes on one.
  - **Tables:** `technologies.csv` and `.parquet`, one row per (domain, technology): categories, versions (several joined as `1.12.4; 3.6.0`), confidence, tier, group, signals.
- **Why:** each number in the summary says what it counts and over which domains, so it can be compared and checked, and nothing is hidden in a single total.
- **Consequences:**
  - Run 1 (EXPERIMENTS): core 347, extended 414 (41 only in DNS, 10 only inferred, 16 themes). The comparison with Veridion's 477 depends on how they counted, which is why both numbers and their groups are reported.
  - `detections.jsonl` is large (about 4 MB) because every detection carries its evidence.

## D-019 Ground truth: a blind set for recall, a random review set for precision
- **Date:** 2026-11-19
- **Context:** Counts say nothing about quality. Precision (how many detections are right) and recall (how many of a site's real technologies we find) need a human-checked sample. A sample is only honest if it is not chosen by looking at results, and if the human's list is not influenced by the tool.
- **Options considered and decision:**
  - **Two sets, two purposes**, in `data/ground_truth.json`:
    - **blind** (recall): every 20th input row (rows 20, 40, ..., 200), picked by position before the tool existed. For each, the human records every technology they can confirm in DevTools, and where, before seeing any tool output. Recall = what the tool found of that list.
    - **review** (precision): 10 domains drawn at random among those with status `ok`, not redirected to another site, and not in the blind set, with Python's `random.Random(20261119).sample(...)` over the domains in input order. The seed is fixed so anyone can redraw the same 10. Rejected: hand-picking or picking by group, which would choose easy or interesting sites.
  - **Labelling** (`techdetect evaluate label <domain>`): shows each detection with its evidence; the human marks it correct / wrong / unsure (with an optional note) and adds technologies the tool missed. For a blind domain, the tool first asks for the human's own list and saves it, and only then shows its detections, so the list cannot be shaped by them. Technologies the human notices only after seeing the tool's output are marked `after_seeing_tool` and left out of the blind recall.
  - **Names:** typed names are matched to the rule names (case-insensitive, with suggestions for near misses), so "google analytics" and the tool's "Google Analytics" are the same label. A technology the rules do not know is kept as typed with `in_rules: false`: a miss that no rule could have caught is a different finding from a rule that failed.
  - **Who labelled:** every label has `found_by`, so a second opinion (e.g. another person, or a BuiltWith lookup confirmed in DevTools) can be added later without redoing anything. Every answer is saved at once, atomically.
  - **Blind lists imported from the notes** (`techdetect evaluate import-blind`): the blind domains were inspected into `docs/manual_inspect_domains.md` (1 domain by me in Chrome DevTools, `found_by: "me"`; 9 by a separate AI session that never saw the tool's output, using a headless browser, curl and DNS lookups, `found_by: "blind_session"`). The import reads each domain's technology table, plugin / theme / app list and the DNS lines naming a provider, keeps versions and "where seen", and matches names to the rules; 6 checked renames (e.g. "Google Fonts" = "Google Font API", each checked against the rule's website) are the only aliases. Names no rule has stay as written with `in_rules: false` (26 of 119): misses no rule could catch, reported apart from rule failures. Recall is therefore "against an independent inspection, mostly by a separate AI session"; the README must say so.
  - **Verdicts on the tool's detections** (all 347 detections of the 20 domains) were made by two separate automated reviewers, one per set, not by the author of the tool: each judged every detection against its evidence and the saved raw data, with a one-sentence reason. Their disagreement on platform infrastructure led to the written definitions in `docs/DEFINITIONS.md` (D-022), which a person decided; the verdicts follow them.
- **Why:** blind labels measure recall without the tool's influence; a random review sample measures precision without choosing easy cases.
- **Consequences:**
  - Recall is "recall against what one careful human found in DevTools": technologies the human did not recognise are missing from the list, so recall is likely overestimated. The README must say so.
  - 10 + 10 domains are a small sample: every rate will be reported with its counts (e.g. "90% (45/50)"), never as a bare percentage.

## D-020 Candidate rules: nothing written by a script or an AI is counted before a human accepts it
- **Date:** 2026-11-19
- **Context:** Coverage will grow with rules we write ourselves, many of them drafted by scripts or AI agents (DNS records, unknown third-party hosts). A wrong rule silently adds false detections to every domain it matches; an AI can also invent a plausible vendor URL and quote.
- **Options considered and decision:**
  - **Two folders:** proposed rules go to `fingerprints/candidates/` (webappanalyzer's format plus review fields: `status`, `seen_on`, `examples`, `source_url`, `source_quote`, `source_verified`, `origin`, `review_note`); reviewed rules live in `fingerprints/custom/`, which the normal run loads. Rejected: writing agent rules straight into `custom/` and reviewing later, which counts them before anyone looked.
  - **Review:** `techdetect candidates review` shows each `proposed` rule with its source, whether the source was actually checked (`source_verified`), its origin (`data`, `entity_list`, or `llm_suggested`, the least trusted), and what the rule really matches on its example domains' saved data (offline). Only the human's answer sets `accepted` or `rejected`; skipping leaves it `proposed`. Rules can also be edited by hand in the JSON.
  - **Promote:** `techdetect candidates promote` moves only `accepted` rules into `custom/` (same file name), adds `reviewed_by: human` and the date, never overwrites a rule already there, and prints what moved. `proposed` and `rejected` rules stay in `candidates/`.
  - **A second lock in the loader:** a rule in `custom/` without `reviewed_by: "human"` is not loaded and is listed in the load report. This also catches a rule copied into `custom/` by hand without review.
  - Candidates can still be tried before review with `--include-candidates` (only `proposed` or `accepted`, never `rejected`), for measuring; the normal run never loads them.
- **Why:** `source_verified` and the live preview turn "the AI says so" into something a human can check in a minute; the stamp makes "reviewed" a property of every counted rule, not a habit.
- **Consequences:**
  - Every custom rule carries who reviewed it and when; the rule-set version hash changes with every promotion, so runs before and after are told apart.
  - Our own test rules carry `reviewed_by: "human"` too, since they go through the same loader.

## D-021 Failure analysis: parked and placeholder statuses, block pages re-read offline
- **Date:** 2026-11-20
- **Context:** The failure report (`scripts/failure_report.py` -> `docs/FAILURES.md`, every group then checked by hand in a browser) showed three ways the domain status misled: 8 domains counted as working sites were a parking lander or a default page; 5 bot-protection block pages were reported as plain HTTP errors; 3 dead sites behind Cloudflare were reported as HTTP errors.
- **Options considered and decision** (each rule built only from the real examples in the data, with a test per example):
  - **`parked`:** the page loaded but ended on an ad / parking lander (`/lander`, `lander_v4.php`, `middle.html?impId=`; 3 domains). **`placeholder`:** the whole title is a hosting company's or a device's default page or "coming soon" ("Default page", "Site Hospedado na KingHost", "Hello! Welcome to Synology Web Station!", "Coming Soon", "無効なURLです"; 5 domains). Titles are matched whole, so "Coming soon: our winter collection" stays a real site. Both statuses are left out of every count and average (like redirects): what is on those pages belongs to the parking service or the host, not to the domain's owner. Rejected: the "domain for sale" keyword list planned earlier; it matched none of the 200 pages.
  - **Block pages:** `detect` re-reads every saved failed page with the current challenge rules, offline. Akamai's "Access Denied" page is now recognised by its own text (title plus its "Reference #18...." code) or its `akamai-grn` header, since most such responses carry no `server: AkamaiGHost` header; Cloudflare's block page ("Attention Required! | Cloudflare") is recognised too. 5 domains moved from `failed:http_error` to `bot_challenge`. A plain "403 Forbidden" that does not say who blocked stays an HTTP error (3 domains). The collector uses the same improved rules from now on.
  - **Dead behind a CDN:** Cloudflare's error 1001 page ("DNS resolution error": the name behind the CDN no longer resolves) now gives `failed:dns_error` (3 domains).
  - All three are detect-only: the saved pages are re-read, nothing is crawled again.
- **Why:** averages and "domains with technologies" must be about real websites; a bot wall must read as "the site exists but blocked us", not "the site is broken".
- **Consequences:**
  - On Run 0's data: 155 ok (was 163), 10 bot_challenge (5), 10 dns_error (7), 15 http_error (23), 3 parked, 5 placeholder; core per domain 15.3 over 150 ok domains (was 14.8 over 156); core and extended counts unchanged (347 / 414).
  - Checking by hand found a collector bug the statuses cannot fix: on 6 domains the certificate is for another name, the fetcher looked anyway, and took the host's default page while the real site is on plain `http://` (`docs/FAILURES.md`, "Checked by hand"). Two of the 5 placeholders were such cases.
  - **Fixed in the collector (Run 2):** a page reached only by ignoring a certificate made *for another name* (Python: "Hostname mismatch"; Chromium: `ERR_CERT_COMMON_NAME_INVALID`) no longer ends the search; the remaining addresses are tried and a verified `https://` or plain `http://` page is preferred, the unchecked page being kept only if nothing else works. A self-signed certificate for the right name is still accepted at once (it is the site's own page). `http://www.<domain>` is now the fourth address tried. All 6 domains now give their real site.
  - **Redirects to the same company count (same-name rule):** a domain redirected to another registered domain is credited with its technologies only if both names are the same company's: equal once the ending, hyphens and dots are removed (seringa-safaris.com -> seringasafaris.com), or one contains the other, at least 5 characters (velvety.eu -> velvety-cosmetics.com). A renamed company (fsbwyoming.com -> myfrbank.com) or a parent company (allegrocreditbeta.com -> synchrony.com) stays out and listed: crediting a parent's corporate website to a small brand would mislead a salesperson, so the rule errs on the side of leaving out. Built from the 5 real redirects, with a test each. For quality metrics (precision / recall) every redirected domain is included anyway: the tool and the label look at the same page.
  - **"Domain for sale" pages are `parked` too:** the title names the domain and says "for sale" / "steht zum Verkauf", or the page says "this domain is for sale" / "buy this domain" (phrases about the domain itself, so a shop's items for sale never match). Built from weissbauchpapageien.de, whose parking service switched from an ad lander to a sale page between two crawls.
  - Some sites redefine built-ins (Wix redefines `Object`, `Number`...), which then appear among their variables: that is the site's own doing, not noise from the baseline.

## D-022 Definitions for labelling, and a hosted platform's infrastructure is not the site's
- **Date:** 2026-11-20
- **Context:** The detections of the 20 ground-truth domains were labelled by two separate automated reviewers (one per set; neither saw the other's verdicts), each judging every detection against its evidence and the saved raw data. They disagreed on one kind of case: on a Wix site, Amazon S3 and AWS headers on Wix's own files were labelled wrong ("supplier infrastructure"); on a Weebly site, the same kind of headers, and Cloudflare in front of the page, were labelled correct ("served on the site's own responses"). Precision measured with inconsistent labels means nothing, so the rules for "what counts" are now written down once, in `docs/DEFINITIONS.md`, and every label, rule, test and piece of code follows them.
- **Options considered:**
  - (a) Infrastructure counts only when the site's owner chose it. On a hosted website builder (Wix, Weebly, IONOS MyWebsite...), the servers, CDN, caching, storage, bot protection, certificate and the platform's own tracking belong to the platform.
  - (b) Anything served from the site's own address counts, whoever chose it.
- **Decision:** (a). The definitions also fix the other cases the reviewers met: web features (HSTS, HTTP/3, Open Graph...) count when present; page code counts when it runs on the page, whoever loaded it; a mention (text, a security-policy header, a logo) is not use; an inference is correct only when certain (WordPress -> PHP yes, WordPress -> MySQL unsure); a wrong version does not make a detection wrong.
- **Why:** the output is a lead list. "Uses Cloudflare" tells a salesperson the company chose and pays for Cloudflare; for a therapist's Wix site that is false, and the lead is wasted.
- **Consequences:**
  - 14 labels changed from correct to wrong on the 3 platform sites (appelpsychotherapy.com on Wix, massageforworldpeace.com on IONOS MyWebsite, 5rcircleprocess.weebly.com on Weebly); each keeps its original verdict and reason. The same items in the blind hand-made lists (8 entries) are kept but marked `counts: false`, so recall does not ask the tool to find what precision calls wrong.
  - Page code a platform bundles (React on Wix, jQuery on Weebly) still counts. That follows the same "who chose it" logic less strictly; it is kept because the code really runs on the page and because the independent blind lists counted it. To be looked at again together with (a).
  - The detector still reports platform infrastructure: it has no notion of "this site is on a hosted builder" yet. Those detections now count as false positives until a rule change handles them.
  - Precision labels are by automated reviewers following the definitions, with the platform rule decided by a person. The README must say so.
