"""Copy webappanalyzer's rule files at one pinned commit into fingerprints/webappanalyzer/.

Usage:  python scripts/update_fingerprints.py <commit-sha>

Only the files the tool uses are copied (technologies, categories, groups, schema,
LICENSE); the 80 MB of logos are not. fingerprints/VERSION records the commit, its date
and the copy date, so every detection run can say exactly which rules it used.
Updating the rules = running this with a new commit, then a new EXPERIMENTS row.
"""

import io
import json
import shutil
import sys
import tarfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO = "enthec/webappanalyzer"
API = f"https://api.github.com/repos/{REPO}"
TARGET = Path("fingerprints/webappanalyzer")
VERSION_FILE = Path("fingerprints/VERSION")
# Path inside the repository -> path under TARGET.
WANTED_FILES = {
    "src/categories.json": "categories.json",
    "src/groups.json": "groups.json",
    "schema.json": "schema.json",
    "LICENSE": "LICENSE",
}
TECHNOLOGIES_DIR = "src/technologies/"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "techdetect-rules-update"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def wanted_target(member_path: str) -> str | None:
    """Where a file of the archive goes under TARGET, or None if it is not needed.

    Archive paths start with a "<owner>-<repo>-<sha>/" folder. Only known names are
    accepted, so no archive entry can write outside TARGET.
    """
    _, _, inner = member_path.partition("/")
    if inner in WANTED_FILES:
        return WANTED_FILES[inner]
    if inner.startswith(TECHNOLOGIES_DIR):
        name = inner.removeprefix(TECHNOLOGIES_DIR)
        if "/" not in name and name.endswith(".json"):
            return f"technologies/{name}"
    return None


def main(commit: str) -> None:
    info = json.loads(fetch(f"{API}/commits/{commit}"))
    sha = info["sha"]
    archive = tarfile.open(fileobj=io.BytesIO(fetch(f"{API}/tarball/{sha}")), mode="r:gz")

    if TARGET.exists():
        shutil.rmtree(TARGET)  # a file removed upstream must not survive here
    copied = 0
    for member in archive.getmembers():
        target = wanted_target(member.name)
        if target is None or not member.isfile():
            continue
        data = archive.extractfile(member).read()
        path = TARGET / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)  # bytes as published, no line-ending changes
        copied += 1

    VERSION_FILE.write_text(
        f"source: https://github.com/{REPO}\n"
        f"commit: {sha}\n"
        f"commit_date: {info['commit']['committer']['date']}\n"
        f"copied: {datetime.now(UTC).strftime('%Y-%m-%d')}\n",
        encoding="utf-8",
    )
    print(f"copied {copied} files from {REPO}@{sha[:12]} into {TARGET}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
