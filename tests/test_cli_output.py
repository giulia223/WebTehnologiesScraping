import os
import subprocess
import sys
from pathlib import Path


def test_dry_run_prints_idn_names_on_a_non_utf8_stream(tmp_path: Path) -> None:
    """Reproduce the Windows crash: stdout is a pipe whose encoding cannot encode 'ø'.

    PYTHONIOENCODING=ascii is stricter than the cp1250 Windows code page (neither has
    'ø' or 'æ'), so it reproduces the failure on every OS, including Linux CI.
    Runs a real subprocess because the bug lives in the process's real stdout.
    """
    path = tmp_path / "idn.txt"
    path.write_text("xn--kleoghvidevareservice-qfc.dk\nxn--jobvrkstedet-9cb.nu\n", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"}

    result = subprocess.run(
        [sys.executable, "-m", "techdetect.cli", "collect", "--dry-run", "--input", str(path)],
        capture_output=True,
        env=env,
    )

    stderr = result.stderr.decode("utf-8")
    assert result.returncode == 0, stderr
    assert "UnicodeEncodeError" not in stderr
    stdout = result.stdout.decode("utf-8")
    assert "køleoghvidevareservice.dk" in stdout
    assert "jobværkstedet.nu" in stdout
