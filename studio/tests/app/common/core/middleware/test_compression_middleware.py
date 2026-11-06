"""Whole compressible responses are gzipped, large ones off the event loop;
downloads, streams, byte ranges and other types pass through. The bundle's gzip
copies are served by PrecompressedStaticFiles."""

import asyncio
import gzip
import random
import threading

import pytest
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from studio.app.common.core.middleware import compression_middleware
from studio.app.common.core.middleware.compression_middleware import (
    MINIMUM_SIZE,
    THREAD_MINIMUM_SIZE,
    CompressionMiddleware,
    PrecompressedStaticFiles,
    accepts_gzip,
)

GZIP = {"accept-encoding": "gzip, deflate"}
IDENTITY = {"accept-encoding": "identity"}
# Accept-Encoding sent in two fields, the second refusing gzip
GZIP_REFUSED_IN_SECOND_FIELD = [
    (b"accept-encoding", b"*"),
    (b"accept-encoding", b"gzip;q=0"),
]
# Random bytes do not compress, so their gzip form stays above MINIMUM_SIZE
RANDOM_BYTES = random.Random(0).randbytes(4096)
ENCODED = gzip.compress(RANDOM_BYTES)


def _body(size: int) -> bytes:
    return (b"[0.123456789," * (size // 13 + 1))[:size]


def _asgi_get(app, path, raw_headers, messages=None):
    """GET through the ASGI app with these header fields, one scope entry each,
    as a server passes them; the test client joins fields of one name. The
    messages sent go to `messages` as they are sent."""
    messages = [] if messages is None else messages

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "http_version": "1.1",
    }
    asyncio.run(app(scope, receive, send))
    start = Headers(raw=messages[0]["headers"])
    return start, b"".join(m.get("body", b"") for m in messages[1:])


@pytest.fixture
def loop_threads():
    """Thread ids the endpoints ran on: the event loop's, for an async def."""
    return []


@pytest.fixture
def client(tmp_path, loop_threads):
    app = FastAPI()
    app.add_middleware(CompressionMiddleware)
    download = tmp_path / "result.json"
    download.write_bytes(_body(10_000))

    @app.get("/body")
    async def body(size: int, media_type: str = "application/json"):
        loop_threads.append(threading.get_ident())
        return Response(content=_body(size), media_type=media_type)

    @app.get("/download")
    async def download_file():
        return FileResponse(download, filename="result.json")

    @app.get("/stream")
    async def stream():
        chunks = [_body(10_000), _body(10_000)]
        return StreamingResponse(iter(chunks), media_type="application/json")

    @app.get("/encoded")
    async def encoded():
        return Response(
            content=ENCODED,
            media_type="application/json",
            headers={"Content-Encoding": "gzip"},
        )

    @app.get("/range")
    async def byte_range():
        return Response(
            content=_body(10_000),
            status_code=206,
            media_type="application/json",
            headers={"Content-Range": "bytes 0-9999/20000"},
        )

    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "accept_encoding, accepted",
    [
        ("gzip, deflate, br", True),
        ("br;q=1.0, GZIP;q=0.5", True),
        ("deflate, *;q=0.1", True),
        ("gzip;q=0", False),
        ("gzip;q=0, *", False),
        ("*;q=0", False),
        ("identity", False),
        ("", False),
    ],
)
def test_accepts_gzip_reads_the_quality(accept_encoding, accepted):
    headers = Headers({"accept-encoding": accept_encoding})

    assert accepts_gzip(headers) is accepted


@pytest.mark.parametrize(
    "fields, accepted", [(["*", "gzip;q=0"], False), (["identity", "gzip"], True)]
)
def test_accepts_gzip_reads_every_field(fields, accepted):
    headers = Headers(raw=[(b"accept-encoding", field.encode()) for field in fields])

    assert accepts_gzip(headers) is accepted


def test_a_whole_json_body_is_gzipped(client):
    response = client.get("/body", params={"size": 10_000}, headers=GZIP)

    assert response.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in response.headers["vary"].lower()
    assert response.content == _body(10_000)
    sent = int(response.headers["content-length"])
    assert response.num_bytes_downloaded == sent < 10_000


@pytest.mark.parametrize(
    "media_type, compressed",
    [
        ("application/json", True),
        ("text/html; charset=utf-8", True),
        ("text/css", True),
        ("application/javascript", True),
        ("image/svg+xml", True),
        ("application/problem+json", True),
        ("image/png", False),
        ("application/octet-stream", False),
    ],
)
def test_only_compressible_types_are_gzipped(client, media_type, compressed):
    response = client.get(
        "/body", params={"size": 10_000, "media_type": media_type}, headers=GZIP
    )

    assert ("content-encoding" in response.headers) is compressed
    assert response.content == _body(10_000)


@pytest.mark.parametrize(
    "size, compressed", [(MINIMUM_SIZE - 1, False), (MINIMUM_SIZE, True)]
)
def test_a_body_under_the_minimum_size_is_sent_as_is(client, size, compressed):
    response = client.get("/body", params={"size": size}, headers=GZIP)

    assert ("content-encoding" in response.headers) is compressed
    assert response.content == _body(size)


def test_a_client_without_gzip_gets_the_body_as_is_with_vary(client):
    response = client.get("/body", params={"size": 10_000}, headers=IDENTITY)

    assert "content-encoding" not in response.headers
    assert "accept-encoding" in response.headers["vary"].lower()
    assert response.num_bytes_downloaded == 10_000


def test_a_refusal_in_a_second_field_is_honoured():
    app = CompressionMiddleware(
        Response(content=_body(10_000), media_type="application/json")
    )

    headers, body = _asgi_get(app, "/", GZIP_REFUSED_IN_SECOND_FIELD)

    assert "content-encoding" not in headers
    assert body == _body(10_000)


def test_a_download_passes_through(client):
    response = client.get("/download", headers=GZIP)

    assert "content-encoding" not in response.headers
    assert "attachment" in response.headers["content-disposition"]
    assert response.num_bytes_downloaded == 10_000


def test_a_streamed_response_passes_through(client):
    response = client.get("/stream", headers=GZIP)

    assert "content-encoding" not in response.headers
    assert response.content == _body(10_000) * 2


def test_a_byte_range_passes_through(client):
    response = client.get("/range", headers=GZIP)

    assert response.status_code == 206
    assert "content-encoding" not in response.headers
    assert response.headers["content-range"] == "bytes 0-9999/20000"
    assert response.num_bytes_downloaded == 10_000


def test_an_event_stream_starts_before_its_first_event():
    messages = []
    sent_before_first_event = []

    async def events(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        sent_before_first_event.append(len(messages))
        await send({"type": "http.response.body", "body": b"data: 1\n\n"})

    _asgi_get(
        CompressionMiddleware(events), "/", [(b"accept-encoding", b"gzip")], messages
    )

    assert sent_before_first_event == [1]


def test_an_encoded_body_is_not_compressed_again(client):
    assert len(ENCODED) >= MINIMUM_SIZE

    response = client.get("/encoded", headers=GZIP)

    assert response.headers["content-encoding"] == "gzip"
    assert response.num_bytes_downloaded == len(ENCODED)
    assert response.content == RANDOM_BYTES


@pytest.mark.parametrize(
    "size, off_loop", [(THREAD_MINIMUM_SIZE - 1, False), (THREAD_MINIMUM_SIZE, True)]
)
def test_a_large_body_is_compressed_off_the_event_loop(
    client, loop_threads, monkeypatch, size, off_loop
):
    """On the loop, compressing the dialog's preview item stalls every other
    request for about 50 ms even at level 1."""
    compress_threads = []
    gzip_body = compression_middleware._gzip

    def recording_gzip(body):
        compress_threads.append(threading.get_ident())
        return gzip_body(body)

    monkeypatch.setattr(compression_middleware, "_gzip", recording_gzip)

    response = client.get("/body", params={"size": size}, headers=GZIP)

    assert response.headers["content-encoding"] == "gzip"
    assert response.content == _body(size)
    assert len(compress_threads) == len(loop_threads) == 1
    assert (compress_threads != loop_threads) is off_loop


class TestPrecompressedStaticFiles:
    SCRIPT = b"console.log('bundle');\n" * 200

    @pytest.fixture
    def bundle_dir(self, tmp_path):
        (tmp_path / "main.js").write_bytes(self.SCRIPT)
        (tmp_path / "main.js.gz").write_bytes(gzip.compress(self.SCRIPT))
        (tmp_path / "other.js").write_bytes(self.SCRIPT)
        return tmp_path

    @pytest.fixture
    def static_client(self, bundle_dir):
        app = FastAPI()
        app.mount("/static", PrecompressedStaticFiles(directory=bundle_dir))
        with TestClient(app) as test_client:
            yield test_client

    def test_a_gzip_client_gets_the_gzip_copy(self, static_client, tmp_path):
        response = static_client.get("/static/main.js", headers=GZIP)

        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        assert "javascript" in response.headers["content-type"]
        assert "accept-encoding" in response.headers["vary"].lower()
        assert response.content == self.SCRIPT
        gz_size = (tmp_path / "main.js.gz").stat().st_size
        assert response.num_bytes_downloaded == gz_size

    def test_a_client_without_gzip_gets_the_file(self, static_client):
        response = static_client.get("/static/main.js", headers=IDENTITY)

        assert "content-encoding" not in response.headers
        assert response.num_bytes_downloaded == len(self.SCRIPT)

    def test_a_refusal_in_a_second_field_gets_the_file(self, bundle_dir):
        static_files = PrecompressedStaticFiles(directory=bundle_dir)

        headers, body = _asgi_get(
            static_files, "/main.js", GZIP_REFUSED_IN_SECOND_FIELD
        )

        assert "content-encoding" not in headers
        assert body == self.SCRIPT

    def test_a_file_without_a_copy_is_served_as_is(self, static_client):
        response = static_client.get("/static/other.js", headers=GZIP)

        assert response.status_code == 200
        assert "content-encoding" not in response.headers
        assert response.content == self.SCRIPT

    def test_the_copy_has_its_own_etag_and_answers_304(self, static_client):
        compressed = static_client.get("/static/main.js", headers=GZIP)
        plain = static_client.get("/static/main.js", headers=IDENTITY)
        revalidated = static_client.get(
            "/static/main.js",
            headers={**GZIP, "if-none-match": compressed.headers["etag"]},
        )

        assert compressed.headers["etag"] != plain.headers["etag"]
        assert revalidated.status_code == 304
        assert "content-encoding" not in revalidated.headers
        assert "accept-encoding" in revalidated.headers["vary"].lower()

    def test_a_head_request_gets_the_copys_headers(self, static_client, tmp_path):
        response = static_client.head("/static/main.js", headers=GZIP)

        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        gz_size = (tmp_path / "main.js.gz").stat().st_size
        assert response.headers["content-length"] == str(gz_size)
        assert response.content == b""

    def test_a_file_the_middleware_compresses_gets_a_weak_etag(self, bundle_dir):
        """A browser revalidates with both validators; the 304 comes from
        Last-Modified where Starlette compares If-None-Match exactly."""
        app = FastAPI()
        app.add_middleware(CompressionMiddleware)
        app.mount("/static", PrecompressedStaticFiles(directory=bundle_dir))

        with TestClient(app) as test_client:
            compressed = test_client.get("/static/other.js", headers=GZIP)
            plain = test_client.get("/static/other.js", headers=IDENTITY)
            revalidated = test_client.get(
                "/static/other.js",
                headers={
                    **GZIP,
                    "if-none-match": compressed.headers["etag"],
                    "if-modified-since": compressed.headers["last-modified"],
                },
            )

        assert compressed.headers["content-encoding"] == "gzip"
        assert compressed.headers["etag"] == f"W/{plain.headers['etag']}"
        assert not plain.headers["etag"].startswith("W/")
        assert revalidated.status_code == 304

    def test_a_small_copy_passes_the_middleware_as_it_is(self, tmp_path):
        """A copy under one FileResponse chunk reaches the middleware as one
        whole stylesheet body; it must go out as the copy, not gzipped again."""
        rng = random.Random(0)
        stylesheet = "".join(
            f".c{i}{{color:#{rng.randrange(1 << 24):06x}}}\n" for i in range(2000)
        ).encode()
        copy = gzip.compress(stylesheet)
        assert MINIMUM_SIZE <= len(copy) < FileResponse.chunk_size
        (tmp_path / "main.css").write_bytes(stylesheet)
        (tmp_path / "main.css.gz").write_bytes(copy)
        app = FastAPI()
        app.add_middleware(CompressionMiddleware)
        app.mount("/static", PrecompressedStaticFiles(directory=tmp_path))

        with TestClient(app) as test_client:
            response = test_client.get("/static/main.css", headers=GZIP)

        assert response.headers["content-encoding"] == "gzip"
        assert response.num_bytes_downloaded == len(copy)
        assert response.content == stylesheet
