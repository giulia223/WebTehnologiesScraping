"""The provenance header that starts every collected JSON artifact.

It answers "under which conditions was this observed?": when, by which code version,
from which country, and with which request headers. Two crawls that differ in any of
these can legitimately see different technologies.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from techdetect import __version__
from techdetect.config import Config


def _git_commit(start: Path) -> str | None:
    """Commit hash of the repository containing `start`, read from the .git files.

    Reading the files avoids depending on a git executable being installed. It cannot
    tell whether the working tree has uncommitted changes.
    """
    for folder in (start, *start.parents):
        git_dir = folder / ".git"
        if git_dir.is_dir():
            break
    else:
        return None

    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return head or None  # detached HEAD: the file holds the hash itself

    ref = head.removeprefix("ref: ")
    loose = git_dir / ref
    if loose.is_file():
        return loose.read_text(encoding="utf-8").strip() or None

    # Refs can also be packed into one file, one "<hash> <ref>" per line.
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
    return None


def tool_version(start: Path | None = None) -> str:
    """Package version plus the commit, e.g. "0.1.0+3f2a9c1d4e5b"."""
    commit = _git_commit(start or Path(__file__).resolve().parent)
    return f"{__version__}+{commit[:12]}" if commit else __version__


def provenance_header(
    config: Config, user_agent: str | None = None, accept_language: str | None = None
) -> dict[str, Any]:
    """The header for one artifact.

    The browser passes its own values: its User-Agent is Chrome's plus our tag, and the
    Accept-Language it records is the one it was seen sending, not only the configured one.
    """
    return {
        "collected_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool_version": tool_version(),
        "crawl_location": config.crawl_location,
        "user_agent": user_agent or config.user_agent,
        "accept_language": accept_language or config.accept_language,
    }
