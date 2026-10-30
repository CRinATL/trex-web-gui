"""Request-body helpers shared by the routes: the JSON error shape and a size-capped JSON read."""
import json

from fastapi import Request
from fastapi.responses import JSONResponse


def err(status: int, code: str, message: str) -> JSONResponse:
    """A JSON error response, `{error: code, message}`, with HTTP status `status`."""
    return JSONResponse({"error": code, "message": message}, status_code=status)


class BodyRefused(Exception):
    """The body was refused before parsing; `response` is the error to return."""

    def __init__(self, response: JSONResponse) -> None:
        """Carry the ready-made error response."""
        super().__init__(response.body.decode() if isinstance(response.body, bytes) else "refused")
        self.response = response


async def read_json_capped(request: Request, limit: int) -> object:
    """The request body parsed as JSON. Raises BodyRefused: 411 length_required without a valid
    Content-Length, 413 too_large when the declared length or the bytes actually read pass `limit` (reading
    stops at limit + 1 bytes), 422 bad_request when the body is not valid JSON."""
    declared = request.headers.get("content-length", "")
    if not declared.isascii() or not declared.isdigit():
        raise BodyRefused(err(411, "length_required", "Content-Length is required"))
    too_large = BodyRefused(err(413, "too_large", f"the request body is over {limit} bytes"))
    if int(declared) > limit:
        raise too_large
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise too_large
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except (ValueError, RecursionError):          # JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise BodyRefused(err(422, "bad_request", "the request body is not valid JSON")) from None
