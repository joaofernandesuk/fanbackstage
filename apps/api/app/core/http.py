from fastapi import Request


class RequestBodyTooLarge(ValueError):
    """Raised before an untrusted request body is accumulated in memory."""


async def read_limited_body(request: Request, *, max_bytes: int) -> bytes:
    """Read an ASGI request incrementally and stop once ``max_bytes`` is exceeded."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if declared < 0:
            raise ValueError("Invalid Content-Length")
        if declared > max_bytes:
            raise RequestBodyTooLarge("Request body exceeds the configured limit")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise RequestBodyTooLarge("Request body exceeds the configured limit")
        body.extend(chunk)
    return bytes(body)
