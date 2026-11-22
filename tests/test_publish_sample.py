"""Task 4.4: the committed raw evidence sample."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "publish_sample.py"
spec = importlib.util.spec_from_file_location("publish_sample", SCRIPT)
assert spec and spec.loader
publish_sample = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish_sample)


def test_copies_the_domains_without_downloaded_scripts(tmp_path: Path) -> None:
    raw, sample = tmp_path / "raw", tmp_path / "sample"
    (raw / "a.com" / "js").mkdir(parents=True)
    (raw / "a.com" / "static.json").write_text("{}", encoding="utf-8")
    (raw / "a.com" / "js" / "x.js").write_text("code", encoding="utf-8")
    (raw / "b.com").mkdir()
    (sample / "old.com").mkdir(parents=True)  # from an older copy

    copied = publish_sample.copy_sample(raw, sample, ["a.com", "b.com", "missing.com"])

    assert copied == ["a.com", "b.com"]
    assert (sample / "a.com" / "static.json").is_file()
    assert not (sample / "a.com" / "js").exists()
    assert not (sample / "old.com").exists()
    assert (sample / "README.md").is_file()
