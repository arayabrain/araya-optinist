"""open_public_url: a user-supplied URL is fetched from public addresses only, on
every redirect hop, and a server that stops answering ends in an error."""
import socket
import threading
import time
from contextlib import contextmanager

import pytest
import requests

from studio.app.common.core.utils import public_url
from studio.app.common.core.utils.public_url import is_public_address, open_public_url

BODY = b"t,v\n" * 1000


def ok(conn, request, port, released):
    conn.sendall(
        b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
        % len(BODY)
        + BODY
    )


def silent(conn, request, port, released):
    released.wait(5)


def stall_mid_body(conn, request, port, released):
    conn.sendall(
        b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
        % len(BODY)
        + BODY[:100]
    )
    released.wait(5)


def redirect_to(location):
    def respond(conn, request, port, released):
        if request.startswith(b"GET /final "):
            return ok(conn, request, port, released)
        conn.sendall(
            b"HTTP/1.1 302 Found\r\nLocation: %s\r\nContent-Length: 0\r\n"
            b"Connection: close\r\n\r\n" % location.format(port=port).encode()
        )

    return respond


@contextmanager
def server(respond):
    """A server on 127.0.0.1 that answers each connection with respond.
    Yields its port and the requests it received."""
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(0.1)
    port = listener.getsockname()[1]
    received = []
    released = threading.Event()

    def serve():
        while not released.is_set():
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            with conn:
                conn.settimeout(5)
                request = conn.recv(65536)
                received.append(request)
                respond(conn, request, port, released)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port, received
    finally:
        released.set()
        thread.join(timeout=10)
        listener.close()


@pytest.fixture
def loopback_is_public(monkeypatch):
    """The test server stands in for a public host; other loopback addresses
    stay private."""
    monkeypatch.setattr(public_url, "is_public_address", lambda a: a == "127.0.0.1")


@pytest.fixture
def short_timeout(monkeypatch):
    monkeypatch.setattr(public_url, "TIMEOUT", (1, 0.5))


def read_all(res):
    try:
        return b"".join(res.iter_content(chunk_size=1024 * 1024))
    finally:
        res.close()


@pytest.mark.parametrize(
    "address, public",
    [
        ("93.184.215.14", True),
        ("2606:2800:21f:cb07:6820:80da:af6b:8b2c", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # instance metadata
        ("100.64.0.1", False),  # shared address space
        ("0.0.0.0", False),
        ("::1", False),
        ("fe80::1%en0", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),  # IPv4-mapped
        ("::ffff:10.0.0.5", False),
    ],
)
def test_only_global_addresses_are_public(address, public):
    assert is_public_address(address) is public


def test_a_public_address_is_fetched_uncompressed(loopback_is_public):
    with server(ok) as (port, received):
        res = open_public_url(f"http://127.0.0.1:{port}/data.csv")
        assert read_all(res) == BODY
    assert b"accept-encoding: identity" in received[0].lower()


def test_a_host_that_resolves_to_a_private_address_is_refused():
    with server(ok) as (port, received):
        with pytest.raises(requests.ConnectionError, match="not public"):
            open_public_url(f"http://localhost:{port}/data.csv")
    assert received == []


def test_a_host_with_any_private_address_is_refused(monkeypatch):
    def getaddrinfo(host, port, family=0, type=0, *args):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port)),
        ]

    monkeypatch.setattr(public_url.socket, "getaddrinfo", getaddrinfo)
    with pytest.raises(requests.ConnectionError, match="not public"):
        open_public_url("http://data.example.com/data.csv")


def test_a_redirect_to_a_private_address_is_refused(loopback_is_public):
    with server(redirect_to("http://127.0.0.2:{port}/final")) as (port, received):
        with pytest.raises(requests.ConnectionError, match="not public"):
            open_public_url(f"http://127.0.0.1:{port}/data.csv")
    assert len(received) == 1


def test_a_redirect_to_a_public_address_is_followed(loopback_is_public):
    with server(redirect_to("http://127.0.0.1:{port}/final")) as (port, received):
        res = open_public_url(f"http://127.0.0.1:{port}/data.csv")
        assert read_all(res) == BODY
    assert len(received) == 2


def test_a_server_that_never_answers_times_out(loopback_is_public, short_timeout):
    with server(silent) as (port, _):
        started = time.monotonic()
        with pytest.raises(requests.ReadTimeout):
            open_public_url(f"http://127.0.0.1:{port}/data.csv")
    assert time.monotonic() - started < 3


def test_a_body_that_stalls_ends_in_an_error(loopback_is_public, short_timeout):
    with server(stall_mid_body) as (port, _):
        res = open_public_url(f"http://127.0.0.1:{port}/data.csv")
        started = time.monotonic()
        with pytest.raises(requests.ConnectionError, match="timed out"):
            read_all(res)
    assert time.monotonic() - started < 3
