"""Copy the raw artifacts of the 20 labelled domains into data/raw_sample/ (committed).

Usage:  python scripts/publish_sample.py

data/raw/ is not committed (about 200 MB, re-creatable by `techdetect collect`). This
sample lets a reviewer open the exact files every verdict and every metric was based on:
the pages, headers, browser requests, DNS, certificate and robots.txt of the 10 blind and
10 review domains in data/ground_truth.json.

The downloaded script files (js/) are left out: they are copies of other companies' code,
make up most of the size, and nothing in the headline numbers uses them.
"""

import json
import shutil
import sys
from pathlib import Path

RAW = Path("data/raw")
SAMPLE = Path("data/raw_sample")
GROUND_TRUTH = Path("data/ground_truth.json")
LEFT_OUT = ("js",)  # folders not copied

README = """# Raw evidence sample

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
"""


def labelled_domains(ground_truth: Path) -> list[str]:
    sets = json.loads(ground_truth.read_text(encoding="utf-8"))["sets"]
    return sets["blind"] + sets["review"]


def copy_sample(raw: Path, sample: Path, domains: list[str]) -> list[str]:
    """Copy each domain's folder (without LEFT_OUT); returns the domains copied. The sample
    is rebuilt from scratch so no file of an older copy stays behind."""
    shutil.rmtree(sample, ignore_errors=True)
    sample.mkdir(parents=True)
    copied = []
    for domain in domains:
        source = raw / domain
        if not source.is_dir():
            print(f"{domain}: no raw folder, skipped", file=sys.stderr)
            continue
        shutil.copytree(source, sample / domain, ignore=shutil.ignore_patterns(*LEFT_OUT))
        copied.append(domain)
    (sample / "README.md").write_text(README, encoding="utf-8")
    return copied


def main() -> None:
    copied = copy_sample(RAW, SAMPLE, labelled_domains(GROUND_TRUTH))
    size = sum(f.stat().st_size for f in SAMPLE.rglob("*") if f.is_file())
    print(f"{len(copied)} domains -> {SAMPLE} ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
