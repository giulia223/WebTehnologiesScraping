"""Run settings in one place. CLI flags override the defaults below."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from techdetect import __version__

REPO_URL = "https://github.com/giulia223/WebTehnologiesScraping"

# An honest robot label: site owners can see who is crawling and where to read about it.
DEFAULT_USER_AGENT = f"techdetect/{__version__} (+{REPO_URL}; research project)"


@dataclass(frozen=True)
class Config:
    # Frozen so a setting cannot change silently halfway through a run;
    # overrides always create a new Config (see with_overrides).
    input_path: Path = Path("data/input/domains.parquet")
    raw_dir: Path = Path("data/raw")
    output_dir: Path = Path("data/output")

    # Placeholders until Phase 2 measures real fetch times.
    static_timeout_s: float = 15.0
    browser_timeout_s: float = 30.0
    dns_timeout_s: float = 5.0

    # Bounded concurrency keeps the crawler polite to sites.
    concurrency: int = 10

    user_agent: str = DEFAULT_USER_AGENT
    # What a default English Chrome sends: multilingual sites then serve their English
    # version if they have one, so results don't depend on a guessed language.
    accept_language: str = "en-US,en;q=0.9"
    # Country the crawl runs from (ISO code). Sites can serve different content, banners
    # and redirects by country, so it is recorded in every artifact's provenance.
    crawl_location: str = "RO"

    def with_overrides(self, **overrides: Any) -> "Config":
        """Return a copy with the given fields replaced.

        None means "flag not passed on the command line", so it keeps the default.
        """
        given = {name: value for name, value in overrides.items() if value is not None}
        return replace(self, **given)
