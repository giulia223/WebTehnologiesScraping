"""Console output that does not crash on non-ASCII domain names (Windows)."""

import sys


def use_utf8_output() -> None:
    """Switch stdout and stderr to UTF-8 if they use another encoding.

    On Windows, when output goes to a pipe or a file (or a terminal that is not a real
    console), Python encodes text with the legacy code page (e.g. cp1250). That code
    page has no 'ø' or 'æ', so printing a decoded Danish domain name raises
    UnicodeEncodeError. UTF-8 can encode every name.
    """
    for stream in (sys.stdout, sys.stderr):
        # Test runners may replace the streams with objects that cannot be reconfigured.
        reconfigure = getattr(stream, "reconfigure", None)
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if reconfigure is not None and encoding != "utf8":
            reconfigure(encoding="utf-8")
