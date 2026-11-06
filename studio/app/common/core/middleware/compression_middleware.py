"""
Gzip compression of responses

Whole responses of a compressible type are gzipped by CompressionMiddleware;
streamed responses and downloads pass through. The frontend bundle is gzipped
at image build and served by PrecompressedStaticFiles.
"""

import asyncio
import gzip
import mimetypes
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple

import anyio
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Smaller bodies gain too little to pay for the gzip framing
MINIMUM_SIZE = 1024
# Level 1 shrinks the DataView dialog's items about 2.6x; level 6 gains under
# 20 % more for about 8x the CPU, which the API shares with workflow runs
COMPRESS_LEVEL = 1
# Under this size a body compresses in well under a millisecond, on the loop
THREAD_MINIMUM_SIZE = 64 * 1024

# Besides text/*
COMPRESSIBLE_MEDIA_TYPES = frozenset(
    {
        "application/javascript",
        "application/json",
        "application/xml",
        "image/svg+xml",
    }
)

# zlib releases the GIL while it compresses, so a thread leaves the event loop
# free; two per process bound the CPU compression takes
_compress_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gzip")


def accepts_gzip(headers: Headers) -> bool:
    """Whether the request's Accept-Encoding allows gzip; q=0 refuses it."""
    # A header sent in several fields reads as their values joined by commas
    accept_encoding = ",".join(headers.getlist("accept-encoding"))
    qualities = {}
    for coding in accept_encoding.split(","):
        name, *params = [part.strip() for part in coding.split(";")]
        quality = 1.0
        for param in params:
            key, _, value = param.partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        qualities[name.lower()] = quality
    return qualities.get("gzip", qualities.get("*", 0.0)) > 0


def _is_compressible(headers: Headers) -> bool:
    """A type that compresses, not encoded yet, and not a download."""
    if "content-encoding" in headers or "content-disposition" in headers:
        return False
    media_type = headers.get("content-type", "").partition(";")[0].strip().lower()
    return media_type.startswith("text/") or media_type in COMPRESSIBLE_MEDIA_TYPES


def _gzip(body: bytes) -> bytes:
    return gzip.compress(body, COMPRESS_LEVEL, mtime=0)


async def _compress(body: bytes) -> bytes:
    if len(body) < THREAD_MINIMUM_SIZE:
        return _gzip(body)
    return await asyncio.get_running_loop().run_in_executor(
        _compress_executor, _gzip, body
    )


class CompressionMiddleware:
    """
    Pure ASGI middleware that gzips a whole response of a compressible type,
    MINIMUM_SIZE or larger, for a client that accepts gzip. Such a response
    says Vary: Accept-Encoding whether it is compressed or not.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        compressing_send = _CompressingSend(send, accepts_gzip(Headers(scope=scope)))
        await self.app(scope, receive, compressing_send)


class _CompressingSend:
    """
    One response's send. A compressible response's start is held until its
    first body message shows whether the body comes whole.
    """

    def __init__(self, send: Send, gzip_accepted: bool) -> None:
        self.send = send
        self.gzip_accepted = gzip_accepted
        self.held_start: Optional[Message] = None

    async def __call__(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            if _is_compressible(Headers(raw=message["headers"])):
                self.held_start = message
                return
        elif message["type"] == "http.response.body" and self.held_start is not None:
            start, self.held_start = self.held_start, None
            body = message.get("body", b"")
            if not message.get("more_body", False) and len(body) >= MINIMUM_SIZE:
                start, message = await self._encode(start, message, body)
            await self.send(start)
        await self.send(message)

    async def _encode(
        self, start: Message, message: Message, body: bytes
    ) -> Tuple[Message, Message]:
        headers = MutableHeaders(raw=list(start["headers"]))
        headers.add_vary_header("Accept-Encoding")
        if self.gzip_accepted:
            body = await _compress(body)
            headers["Content-Encoding"] = "gzip"
            headers["Content-Length"] = str(len(body))
            message = {**message, "body": body}
        return {**start, "headers": headers.raw}, message


class PrecompressedStaticFiles(StaticFiles):
    """
    StaticFiles that serves a file's gzip copy, `<file>.gz` beside it, to a
    client that accepts gzip; other requests are served as StaticFiles does.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        if scope["method"] in ("GET", "HEAD") and accepts_gzip(Headers(scope=scope)):
            full_path, stat_result = await anyio.to_thread.run_sync(
                self.lookup_path, f"{path}.gz"
            )
            if stat_result and stat.S_ISREG(stat_result.st_mode):
                return self._gzip_file_response(path, full_path, stat_result, scope)
        return await super().get_response(path, scope)

    def _gzip_file_response(
        self, path: str, full_path: str, stat_result: os.stat_result, scope: Scope
    ) -> Response:
        # ETag and Last-Modified come from the copy, so they differ per encoding
        response = FileResponse(
            full_path,
            stat_result=stat_result,
            method=scope["method"],
            media_type=mimetypes.guess_type(path)[0],
            headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"},
        )
        if self.is_not_modified(response.headers, Headers(scope=scope)):
            return NotModifiedResponse(response.headers)
        return response
