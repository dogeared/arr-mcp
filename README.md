# arr-mcp

A small [Model Context Protocol](https://modelcontextprotocol.io) server that puts
the *arr media stack (Sonarr, Radarr, Prowlarr, Jellyseerr, Bazarr) behind clean,
typed tools. An MCP client such as Claude calls those tools directly over each
app's REST API — no browser, no screen-scraping, no VPN into the LAN.

The server holds the API keys itself; the client never sees them. It runs on the
`.104` docker host and is reached from the cloud over an authenticated Cloudflare
Tunnel, so it works whether you're home or driving Claude from your phone.

## Why this exists

Everything Claude previously did on the media stack went through a browser
extension on the Mac — which brought connection-identity churn ("which browser?"),
session/2FA friction, and the VPN + remote-control dance. This server removes the
browser and the Mac from the loop entirely, and replaces UI scraping with fast,
structured API calls.

## Tools

| Area | Tools |
|------|-------|
| Overview | `stack_health` |
| Sonarr | `sonarr_health`, `sonarr_series`, `sonarr_series_detail`, `sonarr_missing_episodes`, `sonarr_search_episodes`*, `sonarr_search_season`*, `sonarr_queue`, `sonarr_blocklist_clear`*, `sonarr_lookup` |
| Radarr | `radarr_health`, `radarr_movies`, `radarr_search_movies`*, `radarr_queue` |
| Prowlarr | `prowlarr_health`, `prowlarr_indexers` |
| Jellyseerr | `jellyseerr_requests` |
| Bazarr | `bazarr_status` |

`*` = write action (triggers a search, or clears a series' blocklist). Everything
else is read-only. There are deliberately **no delete/remove or config-mutation
tools** — that keeps the blast radius small. Add more later as needed.

## Setup

```bash
cp config.example.env .env
# edit .env: real API keys (Settings > General > Security > API Key in each app)
# and a strong MCP_AUTH_TOKEN:  python3 -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose up -d --build
```

Quick local check (should return a 400/406 about a missing MCP session — that
means it's *up* and your token was accepted; a 401 means the token is wrong):

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer <your MCP_AUTH_TOKEN>" \
  http://localhost:8787/mcp
```

### Run without Docker (for development)

```bash
pip install -r requirements.txt
set -a; source .env; set +a
python server.py
```

## Exposing it to Claude

The MCP endpoint is `https://<your-hostname>/mcp`.

1. **Tunnel it.** Either uncomment the `cloudflared` service in
   `docker-compose.yml` and give it its own tunnel, or add a hostname to your
   existing Jellyfin tunnel pointing at `http://arr-mcp:8787` (or
   `http://localhost:8787` from the host). Put **Cloudflare Access** in front of
   the hostname, or rely on the bearer token — ideally both.
2. **Add it as a custom connector in Claude.** Settings → Customize → Connectors 
   → Add custom connector → URL `https://<your-hostname>/mcp`. Provide the bearer
   token as the `Authorization: Bearer <token>` header. 
   Claude will then list the tools above.

## Security notes

- The bearer token (`MCP_AUTH_TOKEN`) is required before the endpoint is ever
  reachable from the internet. With it empty the server prints a warning and
  serves unauthenticated — only acceptable bound to localhost.
- The container binds to `127.0.0.1:8787` by default so nothing is exposed except
  through the tunnel you choose.
- API keys live only in `.env` (git-ignored, docker-ignored). Rotate them in each
  app if they ever leak.
- Scope is intentionally read-heavy. Before adding destructive tools (delete
  series, remove from queue, edit settings), decide whether the convenience is
  worth the risk on an internet-reachable endpoint.

## Files

- `server.py` — the MCP server and all tools (streamable-HTTP transport + bearer auth)
- `arr_client.py` — thin async REST client shared by every service
- `config.py` — builds a client per service from env vars (missing = disabled)
- `Dockerfile` / `docker-compose.yml` — container + optional tunnel
- `config.example.env` — copy to `.env`
