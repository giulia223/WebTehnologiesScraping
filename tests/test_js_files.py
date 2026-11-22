"""Script files downloaded over plain HTTP, and matched offline.

collect/js_fetch.py (download; every response from httpx.MockTransport, no network) and
fingerprints/js_files.py (matching the saved files). No browser anywhere.
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from techdetect.collect.js_fetch import MAX_FILES, collect_js, looks_like_html, script_refs
from techdetect.config import Config
from techdetect.domains import Domain
from techdetect.fingerprints.js_files import GTM_URL_KEY
from techdetect.fingerprints.loader import load_rules
from techdetect.fingerprints.matcher import JS_FILES, SOURCES, RuleIndex, match_domain

REAL_RULES = Path(__file__).parents[1] / "fingerprints"
PAGE = """<html><head>
<script>(function(w,d,s,l,i){j.src='https://www.googletagmanager.com/gtm.js?id='+i;
})(window,document,'script','dataLayer','GTM-ABC1234');</script>
<script src="/assets/app.js"></script>
<script src="https://cdn.example.net/lib.js"></script>
</head><body></body></html>"""
# A GTM container holding a Hotjar tag, and a site bundle that calls Clearout's API.
GTM_BODY = b'var data = {"tags":[{"src":"https:\\/\\/static.hotjar.com\\/c\\/hotjar-1.js"}]};'
APP_BODY = b"fetch('https://api.clearout.io/v2/email_verify/instant')"


def write_static_page(raw: Path, host: str, html: str = PAGE) -> None:
    folder = raw / host
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "static.html").write_text(html, encoding="utf-8")
    static = {
        "domain": host,
        "status": "ok",
        "final_url": f"https://{host}/",
        "html_file": "static.html",
        # The matcher also reads the response headers and cookies of the chosen attempt.
        "chosen_attempt": 1,
        "attempts": [{"steps": [{"url": f"https://{host}/", "headers": []}], "cookies": []}],
    }
    (folder / "static.json").write_text(json.dumps(static), encoding="utf-8")


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "gtm.js" in url:
        return httpx.Response(200, content=GTM_BODY)
    if url.endswith("/assets/app.js"):
        return httpx.Response(200, content=APP_BODY)
    return httpx.Response(404, content=b"<html>not found</html>")


def factory(verify: bool) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def collect(raw: Path, host: str = "site.com") -> dict:
    asyncio.run(collect_js([Domain(1, host, host)], Config(raw_dir=raw), factory))
    return json.loads((raw / host / "js.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------ which files


def test_gtm_containers_first_then_script_tags_in_page_order() -> None:
    refs = script_refs(PAGE, "https://site.com/")
    assert [(r.kind, r.url) for r in refs] == [
        ("gtm_container", "https://www.googletagmanager.com/gtm.js?id=GTM-ABC1234"),
        ("script_src", "https://site.com/assets/app.js"),
        ("script_src", "https://cdn.example.net/lib.js"),
    ]


def test_at_most_max_files_per_domain() -> None:
    page = "".join(f'<script src="/s{i}.js"></script>' for i in range(MAX_FILES + 10))
    assert len(script_refs(page, "https://site.com/")) == MAX_FILES


def test_an_html_page_is_not_a_script() -> None:
    assert looks_like_html(b"  <!DOCTYPE html><html>")
    assert not looks_like_html(b"!function(){}()")


# ------------------------------------------------------------ download and save


def test_files_are_saved_by_hash_and_failures_recorded(tmp_path: Path) -> None:
    write_static_page(tmp_path, "site.com")
    js = collect(tmp_path)
    assert js["status"] == "ok"
    outcomes = {f["url"]: f["outcome"] for f in js["files"]}
    assert outcomes == {
        "https://www.googletagmanager.com/gtm.js?id=GTM-ABC1234": "saved",
        "https://site.com/assets/app.js": "saved",
        "https://cdn.example.net/lib.js": "not_saved: status or empty",
    }
    saved = [f for f in js["files"] if f["outcome"] == "saved"]
    for f in saved:
        assert f["file"] == f"js/{f['sha256']}.js"
        assert (tmp_path / "site.com" / f["file"]).is_file()


def test_no_static_page_means_the_step_failed(tmp_path: Path) -> None:
    (tmp_path / "dead.com").mkdir()
    js = collect(tmp_path, "dead.com")
    assert (js["status"], js["error_category"]) == ("failed", "no_static_page")


def test_files_of_an_older_run_are_removed(tmp_path: Path) -> None:
    write_static_page(tmp_path, "site.com")
    old = tmp_path / "site.com" / "js" / "old.js"
    old.parent.mkdir(parents=True)
    old.write_text("old", encoding="utf-8")
    collect(tmp_path)
    assert not old.exists()


# ------------------------------------------------------------ matching, offline


@pytest.fixture(scope="module")
def index() -> RuleIndex:
    return RuleIndex(load_rules(REAL_RULES))


def evidence_of(raw: Path, index: RuleIndex, name: str, sources: frozenset[str]) -> list:
    matches = match_domain(raw, "site.com", index, sources)
    return [e for e in matches.evidence if e.technology == name]


def test_script_content_rules_read_the_downloaded_file(tmp_path: Path, index: RuleIndex) -> None:
    write_static_page(tmp_path, "site.com")
    collect(tmp_path)
    [evidence] = evidence_of(tmp_path, index, "Clearout", SOURCES | {JS_FILES})
    assert (evidence.signal, evidence.tier) == ("scripts", "observed")
    assert evidence.location == "https://site.com/assets/app.js"
    # Off by default, and then the script-content rules stay "not checked".
    assert evidence_of(tmp_path, index, "Clearout", SOURCES) == []
    assert match_domain(tmp_path, "site.com", index, SOURCES).not_checked["scripts"] > 0
    with_js = match_domain(tmp_path, "site.com", index, SOURCES | {JS_FILES})
    assert "scripts" not in with_js.not_checked


def test_a_url_inside_the_gtm_container_is_declared(tmp_path: Path, index: RuleIndex) -> None:
    write_static_page(tmp_path, "site.com")
    collect(tmp_path)
    [evidence] = evidence_of(tmp_path, index, "Hotjar", SOURCES | {JS_FILES})
    assert (evidence.key, evidence.tier) == (GTM_URL_KEY, "declared")
    assert evidence.location == "https://static.hotjar.com/c/hotjar-1.js"  # escapes undone


def test_urls_inside_an_ordinary_bundle_are_not_matched(tmp_path: Path, index: RuleIndex) -> None:
    # A consent manager's bundle can list hundreds of vendor URLs it never loads.
    write_static_page(tmp_path, "site.com", '<script src="/assets/app.js"></script>')
    folder = tmp_path / "site.com"
    collect(tmp_path)
    js = json.loads((folder / "js.json").read_text(encoding="utf-8"))
    [saved] = [f for f in js["files"] if f["outcome"] == "saved"]
    (folder / saved["file"]).write_text("var vendors=['https://static.hotjar.com/c/x.js'];")
    assert evidence_of(tmp_path, index, "Hotjar", SOURCES | {JS_FILES}) == []
