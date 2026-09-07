"""arr-mcp — a Model Context Protocol server for the *arr media stack.

Exposes typed tools over Sonarr, Radarr, Prowlarr, Jellyseerr and Bazarr so an
MCP client (e.g. Claude) can drive the stack directly via each app's REST API —
no browser, no screen-scraping. The server holds the API keys itself; the client
never sees them.

Transport: streamable HTTP (the transport Claude's custom/remote connectors use).
Auth: a bearer token checked by an ASGI middleware (see config.MCP_AUTH_TOKEN).

Tools live in the `tools/` package, one module per element. Importing each
module registers its @mcp.tool() functions on the shared FastMCP instance
defined in mcp_instance.py.

Run locally:   python server.py
In Docker:     see Dockerfile / docker-compose.yml
"""
from __future__ import annotations

import config

from mcp_instance import mcp

# Importing these modules registers all tools on `mcp` as a side effect.
import tools.common  # noqa: F401,E402
import tools.sonarr  # noqa: F401,E402
import tools.radarr  # noqa: F401,E402
import tools.prowlarr  # noqa: F401,E402
import tools.bazarr  # noqa: F401,E402
import tools.jellyseerr  # noqa: F401,E402


# --- ASGI app with bearer auth ----------------------------------------------
class _BearerAuth:
    """Minimal ASGI middleware: require `Authorization: Bearer <token>` on every
    HTTP request. Pure-ASGI (not BaseHTTPMiddleware) so it doesn't interfere with
    the streamable-HTTP / SSE responses."""

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.token:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        if auth != f"Bearer {self.token}":
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"Unauthorized"})
            return
        await self.app(scope, receive, send)


app = _BearerAuth(mcp.streamable_http_app(), config.MCP_AUTH_TOKEN)


if __name__ == "__main__":
    import uvicorn

    if not config.MCP_AUTH_TOKEN:
        print(
            "WARNING: MCP_AUTH_TOKEN is empty — the endpoint is UNAUTHENTICATED. "
            "Only run this bound to localhost / a private network, never behind a "
            "public tunnel without a token."
        )
    uvicorn.run(app, host=config.HOST, port=config.PORT)
