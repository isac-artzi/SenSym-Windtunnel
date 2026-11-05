"""Request guards for a server that only ever talks to the student's own browser.

* Host check: the Host header must name this computer (localhost, 127.0.0.1, ::1, or
  an explicitly allowed name). Stops DNS-rebinding pages from talking to the server.
* Origin check: requests that change something (POST, PUT, PATCH, DELETE) must come
  from a page served by WindLab itself; ``Sec-Fetch-Site: cross-site`` is refused.
* Body size: every request body is counted while it is read; uploads may be up to the
  STL limit, everything else a few kB. A body without a length is refused (411).
* Response headers: strict Content-Security-Policy (scripts only from this server),
  no sniffing, no framing, no referrer.

Pure ASGI middleware, so it also covers streamed request bodies.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from urllib.parse import urlsplit

log = logging.getLogger("windlab.server")

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
UNSAFE_METHODS = frozenset({b"POST", b"PUT", b"PATCH", b"DELETE"})

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"cache-control", b"no-store"),
]


def host_name(value: str) -> str | None:
    """Hostname part of a Host header value (handles [::1]:8080), lower-case."""
    try:
        parts = urlsplit("//" + value.strip())
        return (parts.hostname or "").lower() or None
    except ValueError:
        return None


class _TooLarge(Exception):
    pass


class GuardMiddleware:
    def __init__(self, app, *, allowed_hosts: Iterable[str] = (), max_body: int = 64 * 1024,
                 max_upload: int = 200 * 1024 * 1024, upload_paths: Iterable[str] = ("/api/geometry",)):
        self.app = app
        self.allowed = LOCAL_HOSTS | {h.lower() for h in allowed_hosts}
        self.max_body, self.max_upload = int(max_body), int(max_upload)
        self.upload_paths = set(upload_paths)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}

        host = host_name(headers.get(b"host", b"").decode("latin-1"))
        if host not in self.allowed:
            return await _reply(send, 421, "This WindLab server only answers its own computer (localhost).")

        method = scope["method"].encode() if isinstance(scope["method"], str) else scope["method"]
        if method in UNSAFE_METHODS:
            if headers.get(b"sec-fetch-site", b"") == b"cross-site":
                return await _reply(send, 403, "Requests from other websites are not allowed.")
            origin = headers.get(b"origin")
            if origin is not None and origin != b"null":
                if host_name(urlsplit(origin.decode("latin-1")).netloc) not in self.allowed:
                    return await _reply(send, 403, "Requests from other websites are not allowed.")
            elif origin == b"null":
                return await _reply(send, 403, "Requests from other websites are not allowed.")

        limit = self.max_upload if scope["path"] in self.upload_paths else self.max_body
        length = headers.get(b"content-length")
        if method in UNSAFE_METHODS:
            if length is None:
                if b"chunked" in headers.get(b"transfer-encoding", b""):
                    return await _reply(send, 411, "Please send the file size with the request.")
            else:
                try:
                    n = int(length)
                except ValueError:
                    return await _reply(send, 400, "Bad request.")
                if n > limit:
                    return await _reply(send, 413, _too_large_message(limit))

        received = 0

        async def limited_receive():
            nonlocal received
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > limit:
                    raise _TooLarge
            return msg

        started = False

        async def send_with_headers(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                own = {k.lower() for k, v in message.get("headers", [])}
                # a route may set its own Content-Security-Policy (the standalone report page: inline styles,
                # data: images, still no scripts); every other security header is always ours
                keep = {b"content-security-policy"} & own
                hs = [(k, v) for k, v in message.get("headers", [])
                      if k.lower() not in dict(SECURITY_HEADERS) or k.lower() in keep]
                message = {**message, "headers": hs + [h for h in SECURITY_HEADERS if h[0] not in keep]}
            await send(message)

        try:
            await self.app(scope, limited_receive, send_with_headers)
        except _TooLarge:
            if not started:
                await _reply(send, 413, _too_large_message(limit))
        except Exception:
            # the app's own handler has normally sent a plain JSON message already (and the
            # framework re-raises afterwards); log it here instead of showing a stack trace
            log.exception("unexpected error on %s %s", scope.get("method"), scope.get("path"))
            if not started:
                await _reply(send, 500, "WindLab hit an unexpected problem. Please try again.")


def _too_large_message(limit: int) -> str:
    if limit >= 2**20:
        return f"The file is too large; the limit is {limit / 2**20:.0f} MB."
    return "The request is too large."


async def _reply(send, status: int, message: str) -> None:
    body = json.dumps({"detail": message}).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                    *SECURITY_HEADERS],
    })
    await send({"type": "http.response.body", "body": body})
