"""Match rules against script files downloaded over plain HTTP (js.json + js/*.js).

The offline half of the script-file step (the download is collect/js_fetch.py). Two kinds of match:

1. `scripts` rules on every file's text. These rules exist for exactly this (a vendor's
   API host or a library's banner inside its code); without downloaded files all 828 of
   them were "not checked". Tier "observed": the page itself asks for the file (as a
   `<script src>` match, also read from the plain page, is "observed").
2. Script URLs written inside a Google Tag Manager container (scriptSrc rules on the URL,
   xhr rules on its host), like the inline URLs of static+. Only the GTM container: it
   lists the tags the site configured, while a general bundle (a consent manager, say)
   can list hundreds of vendor URLs it never loads. Tier "declared", as for inline URLs.

Off by default: detect --js-files, or the ablation's rows.
"""

import json
from pathlib import Path
from typing import Any

from techdetect.fingerprints.matcher import (
    DomainMatches,
    _Matcher,
    match_script_urls,
    urls_in_script_text,
)

ARTIFACT = "js.json"
GTM_URL_KEY = "URL inside the GTM container"


def saved_files(folder: Path) -> list[dict[str, Any]] | None:
    """The files js.json says were saved and are on disk; None if the step did not run or
    found no static page."""
    path = folder / ARTIFACT
    if not path.is_file():
        return None
    js = json.loads(path.read_text(encoding="utf-8"))
    if js.get("status") != "ok":
        return None
    return [f for f in js["files"] if f["outcome"] == "saved" and (folder / f["file"]).is_file()]


def match_js_files(m: _Matcher, folder: Path) -> bool:
    """Add the evidence from one domain's downloaded scripts. True if js.json was used."""
    files = saved_files(folder)
    result: DomainMatches = m.result
    if files is None:
        result.artifacts[ARTIFACT] = "not used: step did not run or no static page"
        return False
    for f in files:
        text = (folder / f["file"]).read_bytes().decode("utf-8", errors="replace")
        m.test(m.index.unkeyed["scripts"], text, ARTIFACT, f["url"])
        if f["kind"] == "gtm_container":
            match_script_urls(m, urls_in_script_text(text, f["url"]), ARTIFACT, GTM_URL_KEY)
    result.artifacts[ARTIFACT] = "used"
    # The `scripts` rules were checked on this domain, so they are no longer "not checked".
    result.not_checked.pop("scripts", None)
    return True
