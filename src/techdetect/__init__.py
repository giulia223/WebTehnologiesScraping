"""techdetect: find the technologies a website uses, with evidence for each one."""

from importlib.metadata import PackageNotFoundError, version

# The version is declared once, in pyproject.toml; read it back from the installed metadata
# so the User-Agent and future provenance headers can never disagree with the package.
try:
    __version__ = version("techdetect")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"
