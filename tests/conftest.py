"""Shared pytest setup (fork v109).

- **No test reaches the internet.** Every socket connection that isn't to this machine (the
  tests' own mock servers on 127.0.0.1) is refused, and so is one to a local HTTP(S) proxy
  (which would forward it out). A test that quietly called a real site used to be slow and
  flaky; now it fails fast and loudly. Set TAGCAST_TESTS_ALLOW_NETWORK=1 to switch this off.
- **Slow tests are marked.** The integration tests that start a real Studio against the mock
  iBroadcast server take ~95 s together. ``pytest -m "not slow"`` skips them for a quick
  check (~35 s); a plain ``pytest`` (and CI) still runs everything.
"""

import os
import socket
from urllib.parse import urlparse

import pytest

SLOW_MODULES = {"test_client", "test_write_reliability"}
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _proxy_ports():
    ports = set()
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(name)
        if value:
            try:
                parsed = urlparse(value if "://" in value else f"http://{value}")
                if parsed.hostname in LOOPBACK or (parsed.hostname or "").startswith("127."):
                    ports.add(parsed.port)
            except ValueError:
                pass
    return ports


_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_blocked_ports = _proxy_ports()


def _allowed(address):
    if not isinstance(address, tuple) or len(address) < 2:
        return True  # a Unix socket path
    host, port = str(address[0]), address[1]
    local = host in LOOPBACK or host.startswith("127.")
    return local and port not in _blocked_ports


def _guarded_connect(self, address):
    if not _allowed(address):
        raise ConnectionRefusedError(f"tests may not use the network (tried {address[0]}:{address[1]})")
    return _original_connect(self, address)


def _guarded_connect_ex(self, address):
    if not _allowed(address):
        return 111  # ECONNREFUSED
    return _original_connect_ex(self, address)


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: integration tests against the mock iBroadcast server (~95 s)")
    if os.environ.get("TAGCAST_TESTS_ALLOW_NETWORK") != "1":
        socket.socket.connect = _guarded_connect
        socket.socket.connect_ex = _guarded_connect_ex


def pytest_unconfigure(config):
    socket.socket.connect = _original_connect
    socket.socket.connect_ex = _original_connect_ex


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.module.__name__.rsplit(".", 1)[-1] in SLOW_MODULES:
            item.add_marker(pytest.mark.slow)
