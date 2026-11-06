import io
from unittest.mock import AsyncMock, Mock

from starlette.datastructures import UploadFile


def upload_request(data=b"", on_read=lambda: None):
    """A request whose multipart body holds the upload, as create_file parses
    it; on_read is called on each read of the body"""

    async def stream():
        on_read()
        yield b""

    async def form():
        on_read()
        return {"file": UploadFile(io.BytesIO(data), size=len(data))}

    request = Mock(headers={})
    request.form = form
    request.stream = stream
    request.close = AsyncMock()
    return request
