"""arr-mcp — a Model Context Protocol server for the *arr media stack.

Exposes typed tools over Sonarr, Radarr, Prowlarr, Jellyseerr and Bazarr so an
MCP client (e.g. Claude) can drive the stack directly via each app's REST API —
no browser, no screen-scraping. The server holds the API keys itself; the client
never sees them.

Transport: streamable HTTP (the transport Claude's custom/remote connectors use).
Auth: a bearer token checked by an ASGI middleware (see config.MCP_AUTH_TOKEN).

Run locally:   python server.py
In Docker:     see Dockerfile / docker-compose.yml
"""
from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

import config

mcp = FastMCP("arr-mcp")


# --- helpers ----------------------------------------------------------------
def _require(client, label: str):
    if client is None:
        raise ValueError(
            f"{label} is not configured. Set its URL and API key env vars and restart."
        )
    return client


def _brief_series(s: dict) -> dict:
    st = s.get("statistics", {}) or {}
    return {
        "id": s.get("id"),
        "title": s.get("title"),
        "year": s.get("year"),
        "monitored": s.get("monitored"),
        "status": s.get("status"),
        "have": st.get("episodeFileCount"),
        "total": st.get("episodeCount"),
        "percentOfEpisodes": st.get("percentOfEpisodes"),
    }


def _brief_queue_item(x: dict) -> dict:
    return {
        "title": x.get("title"),
        "series": (x.get("series") or {}).get("title"),
        "seriesId": (x.get("series") or {}).get("id"),
        "movie": (x.get("movie") or {}).get("title"),
        "status": x.get("status"),
        "trackedDownloadState": x.get("trackedDownloadState"),
        "trackedDownloadStatus": x.get("trackedDownloadStatus"),
        "errorMessage": x.get("errorMessage"),
        "sizeleft": x.get("sizeleft"),
        "timeleft": x.get("timeleft"),
    }


def _is_stuck(x: dict) -> bool:
    return (
        x.get("status") in ("failed", "warning")
        or x.get("trackedDownloadStatus") in ("warning", "error")
    )


# ============================================================================
# HEALTH / OVERVIEW
# ============================================================================
@mcp.tool()
async def stack_health() -> dict:
    """Ping every configured service and report which are up. Cheap and safe;
    use this first to confirm the stack is reachable before other calls."""
    out = {}
    for name, client in config.ALL_SERVICES.items():
        if client is None:
            out[name] = "not_configured"
        else:
            out[name] = "up" if await client.ping() else "DOWN"
    return out


# ============================================================================
# SONARR (TV)
# ============================================================================
@mcp.tool()
async def sonarr_health() -> list[dict]:
    """Sonarr's own health-check warnings/errors (indexers, download clients,
    disk space, etc.). Empty list means Sonarr reports itself healthy."""
    return await _require(config.SONARR, "Sonarr").get("health")


@mcp.tool()
async def sonarr_series(query: Optional[str] = None) -> list[dict]:
    """List tracked TV series (brief: id, title, monitored, have/total counts).
    Optional `query` filters by case-insensitive substring of the title."""
    data = await _require(config.SONARR, "Sonarr").get("series")
    items = [_brief_series(s) for s in data]
    if query:
        q = query.lower()
        items = [s for s in items if q in (s["title"] or "").lower()]
    return sorted(items, key=lambda s: (s["title"] or "").lower())


@mcp.tool()
async def sonarr_series_detail(series_id: int) -> dict:
    """Full detail for one series, plus a per-season have/total breakdown."""
    s = await _require(config.SONARR, "Sonarr").get(f"series/{series_id}")
    seasons = [
        {
            "season": se.get("seasonNumber"),
            "monitored": se.get("monitored"),
            "have": (se.get("statistics") or {}).get("episodeFileCount"),
            "total": (se.get("statistics") or {}).get("episodeCount"),
        }
        for se in s.get("seasons", [])
    ]
    return {**_brief_series(s), "path": s.get("path"), "seasons": seasons}


@mcp.tool()
async def sonarr_missing_episodes(series_id: int) -> list[dict]:
    """Monitored, already-aired episodes for a series that have no file yet —
    i.e. the gaps. Returns season/episode/title/airDate."""
    eps = await _require(config.SONARR, "Sonarr").get(
        "episode", params={"seriesId": series_id}
    )
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    out = []
    for e in eps:
        air = e.get("airDateUtc")
        if not (e.get("monitored") and not e.get("hasFile") and air):
            continue
        try:
            aired = _dt.datetime.fromisoformat(air.replace("Z", "+00:00"))
        except ValueError:
            continue
        if aired < now:
            out.append(
                {
                    "episodeId": e.get("id"),
                    "season": e.get("seasonNumber"),
                    "episode": e.get("episodeNumber"),
                    "title": e.get("title"),
                    "airDate": air[:10],
                }
            )
    return sorted(out, key=lambda x: (x["season"], x["episode"]))


@mcp.tool()
async def sonarr_search_episodes(episode_ids: list[int]) -> dict:
    """Trigger an indexer search for specific episodes by episodeId (get IDs from
    sonarr_missing_episodes). WRITE ACTION: queues downloads if releases match."""
    r = await _require(config.SONARR, "Sonarr").post(
        "command", json={"name": "EpisodeSearch", "episodeIds": episode_ids}
    )
    return {"commandId": r.get("id"), "status": r.get("status")}


@mcp.tool()
async def sonarr_search_season(series_id: int, season_number: int) -> dict:
    """Trigger a full-season search (prefers season packs) for one season.
    WRITE ACTION: may grab a season pack and queue downloads."""
    r = await _require(config.SONARR, "Sonarr").post(
        "command",
        json={
            "name": "SeasonSearch",
            "seriesId": series_id,
            "seasonNumber": season_number,
        },
    )
    return {"commandId": r.get("id"), "status": r.get("status")}


@mcp.tool()
async def sonarr_queue(only_stuck: bool = False) -> dict:
    """Current Sonarr download queue (brief). `only_stuck=True` returns just the
    failed/warning items — useful for spotting imports that need attention."""
    data = await _require(config.SONARR, "Sonarr").get(
        "queue", params={"pageSize": 250, "includeSeries": "true"}
    )
    records = data.get("records", data if isinstance(data, list) else [])
    items = [_brief_queue_item(x) for x in records if (only_stuck is False or _is_stuck(x))]
    return {"count": len(items), "items": items}


@mcp.tool()
async def sonarr_blocklist_clear(series_id: int) -> dict:
    """Clear ALL blocklist entries for one series so previously-failed releases
    can be re-grabbed. WRITE ACTION. Scoped to the given series only."""
    client = _require(config.SONARR, "Sonarr")
    bl = await client.get("blocklist", params={"seriesIds": series_id, "pageSize": 250})
    ids = [b["id"] for b in bl.get("records", [])]
    if ids:
        await client.delete("blocklist/bulk", json={"ids": ids})
    return {"cleared": len(ids)}


@mcp.tool()
async def sonarr_lookup(term: str) -> list[dict]:
    """Search for a series to add (TVDB lookup by name). Read-only; does not add."""
    data = await _require(config.SONARR, "Sonarr").get(
        "series/lookup", params={"term": term}
    )
    return [
        {"title": s.get("title"), "year": s.get("year"), "tvdbId": s.get("tvdbId")}
        for s in data
    ]


# ============================================================================
# RADARR (Movies)
# ============================================================================
@mcp.tool()
async def radarr_health() -> list[dict]:
    """Radarr health-check warnings/errors. Empty list means healthy."""
    return await _require(config.RADARR, "Radarr").get("health")


@mcp.tool()
async def radarr_movies(query: Optional[str] = None, missing_only: bool = False) -> list[dict]:
    """List tracked movies (brief). `query` filters by title substring;
    `missing_only=True` returns only monitored movies without a file."""
    data = await _require(config.RADARR, "Radarr").get("movie")
    out = []
    for m in data:
        if missing_only and (m.get("hasFile") or not m.get("monitored")):
            continue
        out.append(
            {
                "id": m.get("id"),
                "title": m.get("title"),
                "year": m.get("year"),
                "monitored": m.get("monitored"),
                "hasFile": m.get("hasFile"),
            }
        )
    if query:
        q = query.lower()
        out = [m for m in out if q in (m["title"] or "").lower()]
    return sorted(out, key=lambda m: (m["title"] or "").lower())


@mcp.tool()
async def radarr_search_movies(movie_ids: list[int]) -> dict:
    """Trigger an indexer search for specific movies by id. WRITE ACTION."""
    r = await _require(config.RADARR, "Radarr").post(
        "command", json={"name": "MoviesSearch", "movieIds": movie_ids}
    )
    return {"commandId": r.get("id"), "status": r.get("status")}


@mcp.tool()
async def radarr_queue(only_stuck: bool = False) -> dict:
    """Current Radarr download queue (brief). `only_stuck=True` filters to
    failed/warning items."""
    data = await _require(config.RADARR, "Radarr").get(
        "queue", params={"pageSize": 250, "includeMovie": "true"}
    )
    records = data.get("records", data if isinstance(data, list) else [])
    items = [_brief_queue_item(x) for x in records if (only_stuck is False or _is_stuck(x))]
    return {"count": len(items), "items": items}


@mcp.tool()
async def radarr_delete_moviefile(
    movie_id: int, confirm: bool = False, trigger_import: bool = True
) -> dict:
    """Delete a movie's current file so a blocked/pending replacement can import.

    GUARDED WRITE. This is the delete-then-import step for quality swaps Radarr
    won't auto-downgrade (e.g. replacing a Remux with a smaller BluRay/WEB-DL):
    Radarr refuses to import the new file over a higher-ranked existing one, so
    the old file has to be removed first.

    Safety: with `confirm=False` (the default) this deletes NOTHING. It returns a
    preview of exactly what *would* be deleted (title, path, quality, size) plus
    whether a matching download is waiting in the queue. Re-call with
    `confirm=True` to actually delete. Only ever touches the single `movie_id`
    given — never a bulk/library operation.

    `trigger_import=True` (default) fires RefreshMonitoredDownloads afterward so a
    completed replacement already in the queue imports without waiting for the
    next scan.
    """
    client = _require(config.RADARR, "Radarr")
    movie = await client.get(f"movie/{movie_id}")
    title = f"{movie.get('title')} ({movie.get('year')})"
    mf = movie.get("movieFile") or {}
    file_id = mf.get("id") or movie.get("movieFileId")

    q = await client.get("queue", params={"pageSize": 250, "includeMovie": "true"})
    qrecords = q.get("records", q if isinstance(q, list) else [])
    pending = [
        {
            "title": x.get("title"),
            "state": x.get("trackedDownloadState"),
            "status": x.get("trackedDownloadStatus"),
        }
        for x in qrecords
        if x.get("movieId") == movie_id or (x.get("movie") or {}).get("id") == movie_id
    ]

    if not movie.get("hasFile") or not file_id:
        return {
            "movie": title,
            "movieId": movie_id,
            "action": "none",
            "reason": "Movie has no current file to delete.",
            "pendingInQueue": pending,
        }

    preview = {
        "movie": title,
        "movieId": movie_id,
        "fileId": file_id,
        "path": mf.get("relativePath") or mf.get("path"),
        "quality": (((mf.get("quality") or {}).get("quality")) or {}).get("name"),
        "sizeGB": round((mf.get("size") or 0) / (1024 ** 3), 2),
        "pendingInQueue": pending,
    }

    if not confirm:
        preview["action"] = "preview"
        preview["note"] = "Nothing deleted. Re-call with confirm=True to delete this file."
        return preview

    status = await client.delete(f"moviefile/{file_id}")
    result = {**preview, "action": "deleted", "deleteHttpStatus": status}
    if trigger_import:
        try:
            cmd = await client.post(
                "command", json={"name": "RefreshMonitoredDownloads"}
            )
            result["importTriggered"] = {
                "commandId": cmd.get("id"),
                "status": cmd.get("status"),
            }
        except Exception as e:  # noqa: BLE001
            result["importTriggered"] = {"error": str(e)}
    return result


# ============================================================================
# PROWLARR (Indexers)
# ============================================================================
@mcp.tool()
async def prowlarr_health() -> list[dict]:
    """Prowlarr health-check warnings/errors. Empty list means healthy."""
    return await _require(config.PROWLARR, "Prowlarr").get("health")


@mcp.tool()
async def prowlarr_indexers() -> list[dict]:
    """List configured indexers with their enabled state."""
    data = await _require(config.PROWLARR, "Prowlarr").get("indexer")
    return [
        {
            "id": i.get("id"),
            "name": i.get("name"),
            "enable": i.get("enable"),
            "protocol": i.get("protocol"),
            "priority": i.get("priority"),
        }
        for i in data
    ]


# ============================================================================
# JELLYSEERR (Requests)
# ============================================================================
@mcp.tool()
async def jellyseerr_requests(filter: str = "all", take: int = 30) -> Any:
    """Recent media requests. `filter` is one of Jellyseerr's filters
    (all, pending, approved, available, processing, unavailable, failed)."""
    return await _require(config.JELLYSEERR, "Jellyseerr").get(
        "request", params={"take": take, "filter": filter, "sort": "added"}
    )


# ============================================================================
# BAZARR (Subtitles)
# ============================================================================
@mcp.tool()
async def bazarr_status() -> Any:
    """Bazarr system status (reachability + version)."""
    return await _require(config.BAZARR, "Bazarr").get("system/status")


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
