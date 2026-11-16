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
