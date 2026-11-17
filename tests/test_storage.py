import json
import os
from pathlib import Path

import pytest

from techdetect import __version__
from techdetect.collect.provenance import provenance_header, tool_version
from techdetect.collect.storage import sha256_hex, write_atomic, write_json_atomic
from techdetect.config import Config


def test_write_atomic_leaves_no_tmp(tmp_path: Path) -> None:
    target = tmp_path / "d.com" / "static.html"
    write_atomic(target, b"<html>hi</html>")
    assert target.read_bytes() == b"<html>hi</html>"
    assert list(target.parent.iterdir()) == [target]


def test_interrupted_write_leaves_no_final_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(src: object, dst: object) -> None:
        raise OSError("simulated crash before the rename")

    monkeypatch.setattr(os, "replace", crash)
    target = tmp_path / "static.json"
    with pytest.raises(OSError):
        write_json_atomic(target, {"a": 1})
    # Only the .tmp exists: a reader looking for static.json sees "step not finished".
    assert not target.exists()
    assert (tmp_path / "static.json.tmp").exists()


def test_json_is_utf8_and_readable(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    write_json_atomic(target, {"name": "jobværkstedet.nu"})
    assert "jobværkstedet.nu" in target.read_text(encoding="utf-8")
    assert json.loads(target.read_text(encoding="utf-8")) == {"name": "jobværkstedet.nu"}


def test_sha256_hex() -> None:
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_tool_version_reads_loose_ref(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "heads" / "main").write_text("3f2a9c1d4e5b6a7b8c9d\n", encoding="utf-8")
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)

    assert tool_version(nested) == f"{__version__}+3f2a9c1d4e5b"


def test_tool_version_reads_packed_ref(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "packed-refs").write_text(
        "# pack-refs with: peeled\naaaabbbbccccdddd refs/heads/main\n", encoding="utf-8"
    )
    assert tool_version(tmp_path) == f"{__version__}+aaaabbbbcccc"


def test_tool_version_without_git(tmp_path: Path) -> None:
    # tmp_path normally lives outside any repository.
    if any((p / ".git").exists() for p in (tmp_path, *tmp_path.parents)):
        pytest.skip("temporary folder is inside a git repository")
    assert tool_version(tmp_path) == __version__


def test_provenance_header_fields() -> None:
    header = provenance_header(Config())
    assert list(header) == [
        "collected_at",
        "tool_version",
        "crawl_location",
        "user_agent",
        "accept_language",
    ]
    assert header["collected_at"].endswith("Z")
    assert header["accept_language"] == "en-US,en;q=0.9"
