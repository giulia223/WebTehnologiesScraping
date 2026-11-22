# Raw evidence sample

The raw artifacts of the 20 domains labelled by hand in `data/ground_truth.json` (10 blind,
10 review), copied from `data/raw/` by `scripts/publish_sample.py`. Precision and recall in
`docs/METRICS.md` were measured on exactly these files.

Per domain: `static.json` + `static.html` (plain HTTP fetch), `browser.json` +
`rendered.html` + `screenshot.jpg` (headless Chromium: requests, cookies, JS variables),
`dns.json`, `tls.json`, `wellknown.json` + `robots.txt`. Every JSON starts with a
provenance header (when, which tool version, from where). Downloaded script files (`js/`)
are left out.

Detection runs offline on this folder too:
`techdetect detect --raw data/raw_sample --output-dir <somewhere> --allow-partial`.
