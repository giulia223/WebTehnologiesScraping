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
