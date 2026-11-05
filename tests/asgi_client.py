"""Minimal synchronous ASGI client with the subset of the TestClient API the tests use.

FastAPI's TestClient needs httpx, which is not installed in the dev container's
read-only venv yet (it is listed in the ``dev`` extra). ``make_client`` returns a real
TestClient when httpx is importable and this fallback otherwise.

Both carry the ``X-WindLab-Disclaimer`` header by default, as the page does once the
educational-use notice has been accepted; ``make_client(app, accept_disclaimer=False)``
gives a client without it (tests/test_app.py checks that a run is then refused).
"""

from __future__ import annotations

import asyncio
import json as _json
from urllib.parse import urlencode

from windlab import disclaimer

ACCEPT_HEADERS = {disclaimer.HEADER: disclaimer.VERSION}


class Response:
    def __init__(self, status: int, headers: list[tuple[bytes, bytes]], body: bytes):
        self.status_code = status
        self.headers = {k.decode().lower(): v.decode() for k, v in headers}
        self.content = body

    def json(self):
        return _json.loads(self.content)

    @property
    def text(self) -> str:
        return self.content.decode()


class ASGIClient:
    def __init__(self, app, host: str = "localhost:8080", headers: dict | None = None):
        self.app, self.host = app, host
        self.default_headers = dict(headers or {})

    def request(self, method, url, *, params=None, json=None, content=None, headers=None, chunk=65536):
        path, _, query = url.partition("?")
        if params:
            query = (query + "&" if query else "") + urlencode(params)
        hdrs = {"host": self.host, **{k.lower(): v for k, v in self.default_headers.items()}}
        body = b""
        if json is not None:
            body = _json.dumps(json).encode()
            hdrs["content-type"] = "application/json"
        elif content is not None:
            body = content
            hdrs["content-type"] = "application/octet-stream"
        if method in ("POST", "PUT", "PATCH") and "content-length" not in {k.lower() for k in (headers or {})}:
            hdrs["content-length"] = str(len(body))
        for k, v in (headers or {}).items():
            if v is None:
                hdrs.pop(k.lower(), None)
            else:
                hdrs[k.lower()] = v
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
            "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
            "root_path": "", "headers": [(k.encode(), str(v).encode()) for k, v in hdrs.items()],
            "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8080),
        }
        parts = [body[i : i + chunk] for i in range(0, len(body), chunk)] or [b""]
        status, out_headers, out = 0, [], []

        async def run():
            nonlocal status, out_headers
            queue = list(parts)
            never = asyncio.Event()

            async def receive():
                if queue:
                    b = queue.pop(0)
                    return {"type": "http.request", "body": b, "more_body": bool(queue)}
                await never.wait()  # a real client stays connected until the response is complete
                return {"type": "http.disconnect"}

            async def send(msg):
                nonlocal status, out_headers
                if msg["type"] == "http.response.start":
                    status, out_headers = msg["status"], msg.get("headers", [])
                elif msg["type"] == "http.response.body":
                    out.append(msg.get("body", b""))

            await self.app(scope, receive, send)

        asyncio.run(run())
        return Response(status, out_headers, b"".join(out))

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def delete(self, url, **kw):
        return self.request("DELETE", url, **kw)


def make_client(app, accept_disclaimer: bool = True):
    headers = ACCEPT_HEADERS if accept_disclaimer else {}
    try:
        import httpx  # noqa: F401
        from fastapi.testclient import TestClient
    except ImportError:
        return ASGIClient(app, headers=headers)
    return TestClient(app, base_url="http://localhost:8080", headers=headers)
