"""Atomic file writes shared by every collector.

A file is written to `<name>.tmp`, then renamed onto `<name>` with os.replace. The rename
happens completely or not at all, so a crash mid-write leaves at most a `.tmp` file,
never a half-written artifact that detection would read as if it were complete.
Collectors write their HTML first and their JSON last: a JSON that exists means its
step finished.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def domain_dir(raw_dir: Path, host: str) -> Path:
    """Folder holding every artifact of one domain: data/raw/<host>/."""
    return raw_dir / host


def sha256_hex(data: bytes) -> str:
    """Fingerprint of a saved file, so anyone can check the proof was not altered."""
    return hashlib.sha256(data).hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        # Make sure the bytes are on disk before the rename makes the file "official".
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_json_atomic(path: Path, obj: dict[str, Any]) -> None:
    # ensure_ascii=False keeps decoded names and header values readable in the file.
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    write_atomic(path, text.encode("utf-8"))
