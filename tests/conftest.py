import socket

import pytest

# The asyncio event loop on Windows talks to itself through a socket pair on 127.0.0.1,
# so loopback connections must stay allowed. Nothing leaves the machine.
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to reach the internet or resolve a name.

    Loading and detection must work offline, and collector tests use httpx.MockTransport;
    this makes an accidental real request (e.g. tldextract downloading a fresh suffix
    list, or a test forgetting the mock) a failure instead of a silent fetch.
    """
    real_connect = socket.socket.connect

    def guarded_connect(sock: socket.socket, address: object) -> None:
        if isinstance(address, tuple) and address[0] in _LOOPBACK:
            return real_connect(sock, address)
        raise RuntimeError(f"network access is not allowed in tests: {address!r}")

    def blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError("network access is not allowed in tests")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
