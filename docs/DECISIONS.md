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
