import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to open a network connection or resolve a name.

    Loading and detection must work offline; this makes an accidental download
    (e.g. tldextract fetching a fresh suffix list) a test failure, not a silent fetch.
    """

    def blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError("network access is not allowed in tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
