"""Build a domain folder in the layout `collect` writes (static.json, browser.json...),
for matcher and combine tests. The HTML pages come from tests/fixtures/pages/."""

import json
import shutil
from pathlib import Path
from typing import Any

from techdetect.fingerprints.matcher import DomainMatches, RuleIndex, match_domain

PAGES = Path(__file__).parent / "fixtures" / "pages"


class Site:
    """Writes one domain's folder in the layout `collect` produces."""

    def __init__(self, raw_dir: Path, host: str) -> None:
        self.raw_dir = raw_dir
        self.host = host
        self.folder = raw_dir / host
        self.folder.mkdir(parents=True)

    def _write(self, name: str, data: dict[str, Any]) -> None:
        (self.folder / name).write_text(json.dumps(data), encoding="utf-8")

    def static(
        self,
        page: str | Path,
        headers: list[list[str]] | None = None,
        cookies: list[dict[str, Any]] | None = None,
        status: str = "ok",
        error_category: str | None = None,
        final_url: str | None = None,
    ) -> "Site":
        # A bare name is a file in tests/fixtures/pages/.
        shutil.copy(page if isinstance(page, Path) else PAGES / page, self.folder / "static.html")
        final_url = final_url or f"https://{self.host}/"
        self._write(
            "static.json",
            {
                "domain": self.host,
                "status": status,
                "error_category": error_category,
                "final_url": final_url,
                "chosen_attempt": 1,
                "html_file": "static.html",
                "attempts": [
                    {
                        "steps": [{"url": final_url, "headers": headers or []}],
                        "cookies": cookies or [],
                    }
                ],
            },
        )
        return self

    def browser(
        self,
        page: str | Path,
        js: dict[str, Any] | None = None,
        asked: list[str] | None = None,
        requests: list[dict[str, Any]] | None = None,
        cookies: list[dict[str, Any]] | None = None,
        headers: list[list[str]] | None = None,
    ) -> "Site":
        shutil.copy(page if isinstance(page, Path) else PAGES / page, self.folder / "rendered.html")
        js = js or {}
        self._write(
            "browser.json",
            {
                "domain": self.host,
                "status": "ok",
                "error_category": None,
                "final_url": f"https://{self.host}/",
                "html_file": "rendered.html",
                "main_document_headers": headers or [],
                "cookies": cookies or [],
                "requests": requests or [],
                "js_globals": js,
                "js_chains_asked": asked if asked is not None else sorted(js),
            },
        )
        return self

    def dns(self, *queries: tuple[str, str, list[str]]) -> "Site":
        records = [
            {"name": name, "type": rtype, "status": "ok", "records": values}
            for name, rtype, values in queries
        ]
        self._write("dns.json", {"domain": self.host, "status": "ok", "queries": records})
        return self

    def tls(self, organization: str, hostname_match: bool) -> "Site":
        certificate = {"issuer": [["organizationName", organization], ["commonName", "R11"]]}
        self._write(
            "tls.json",
            {
                "status": "ok",
                "probed_host": self.host,
                "hostname_match": hostname_match,
                "certificate": certificate,
            },
        )
        return self

    def match(self, index: RuleIndex) -> DomainMatches:
        return match_domain(self.raw_dir, self.host, index)


def cookie(name: str, domain: str) -> dict[str, Any]:
    return {"name": name, "value": "1", "domain": domain, "path": "/"}
