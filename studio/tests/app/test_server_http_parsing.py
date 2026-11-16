"""The API server refuses a request hidden in a malformed chunked body.

uvicorn runs without httptools here, so h11 parses every request. Below 0.16,
h11 took any two bytes after a chunk's data as its line end (CVE-2025-43859):
a body that a front proxy reads as one request could carry a second request to
the app. The server runs as ``studio/server.py`` runs it, with a stand-in app:
the parser acts before any app code.
"""

import socket
import threading
import time
from contextlib import contextmanager

import uvicorn

HEAD = b"Host: localhost\r\nTransfer-Encoding: chunked\r\n\r\n"

WELL_FORMED = (
    b"GET /one HTTP/1.1\r\nConnection: close\r\n" + HEAD + b"5\r\nAAAAA\r\n0\r\n\r\n"
)

# "XX" stands where the 5-byte chunk's line end belongs. Read as h11 < 0.16
# reads it, the body ends at the "0" chunk and "GET /two" is a second request.
SMUGGLING = (
    b"GET /one HTTP/1.1\r\n"
    + HEAD
    + b"5\r\nAAAAAXX2\r\n45\r\n0\r\n\r\n"
    + b"GET /two HTTP/1.1\r\nConnection: close\r\n"
    + HEAD
    + b"0\r\n\r\n"
)


def path_recorder(paths):
    """An ASGI app that records each request's path and answers 200."""

    async def app(scope, receive, send):
        paths.append(scope["path"])
        while (await receive()).get("more_body"):
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return app


@contextmanager
def serving(app):
    """Runs uvicorn on a loopback port in a thread; yields the port."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    # the protocol is left to uvicorn, as in production; no logging config,
    # so the test process's loggers stay as they are
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_config=None))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive(), "the server stopped while starting"
            assert time.monotonic() < deadline, "the server did not start"
            time.sleep(0.01)
        yield sock.getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


def exchange(port, request):
    """Sends the raw request; returns every byte the server sends before closing."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall(request)
        response = b""
        while chunk := conn.recv(65536):
            response += chunk
    return response


def test_a_well_formed_chunked_request_reaches_the_app():
    paths = []
    with serving(path_recorder(paths)) as port:
        response = exchange(port, WELL_FORMED)

    assert response.startswith(b"HTTP/1.1 200 "), response
    assert paths == ["/one"]


def test_a_request_hidden_in_a_malformed_chunked_body_never_reaches_the_app():
    paths = []
    with serving(path_recorder(paths)) as port:
        response = exchange(port, SMUGGLING)

    assert "/two" not in paths, "the hidden request reached the app"
    assert response.startswith(b"HTTP/1.1 400 "), response
