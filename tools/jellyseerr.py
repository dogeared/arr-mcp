"""Jellyseerr (Requests) tools."""
from __future__ import annotations

from typing import Any, Optional

from mcp_instance import mcp

import config
from tools.common import _require


# ============================================================================
# JELLYSEERR (Requests) — existing tools
# ============================================================================
@mcp.tool()
async def jellyseerr_requests(filter: str = "all", take: int = 30) -> Any:
    """Recent media requests. `filter` is one of Jellyseerr's filters
    (all, pending, approved, available, processing, unavailable, failed)."""
    return await _require(config.JELLYSEERR, "Jellyseerr").get(
        "request", params={"take": take, "filter": filter, "sort": "added"}
    )


# ============================================================================
# JELLYSEERR (Requests) — new management tools
# ============================================================================
@mcp.tool()
async def jellyseerr_request_detail(request_id: int) -> Any:
    """Full detail for one media request (READ-ONLY)."""
    return await _require(config.JELLYSEERR, "Jellyseerr").get(f"request/{request_id}")


@mcp.tool()
async def jellyseerr_update_request(
    request_id: int, action: str, confirm: bool = False
) -> dict:
    """Approve or decline a media request.

    GUARDED WRITE. `action` is one of 'approve' or 'decline'. With confirm=False
    (default) changes NOTHING — returns a preview. Re-call with confirm=True to
    POST request/{id}/{action}.
    """
    client = _require(config.JELLYSEERR, "Jellyseerr")
    action = (action or "").lower()
    if action not in ("approve", "decline"):
        return {
            "action": "error",
            "reason": "action must be 'approve' or 'decline'.",
        }
    preview = {"requestId": request_id, "requestedAction": action}
    if not confirm:
        preview["action"] = "preview"
        preview["note"] = f"Nothing changed. Re-call with confirm=True to {action} this request."
        return preview
    r = await client.post(f"request/{request_id}/{action}")
    return {"action": action, "requestId": request_id, "result": r}


@mcp.tool()
async def jellyseerr_media_search(query: str, limit: int = 20) -> dict:
    """Search Jellyseerr for media (READ-ONLY).

    Returns brief rows: title, mediaType, tmdbId, year, status.
    """
    client = _require(config.JELLYSEERR, "Jellyseerr")
    data = await client.get("search", params={"query": query})
    results = data.get("results", data if isinstance(data, list) else [])
    out = []
    for r in results[:limit]:
        date = r.get("releaseDate") or r.get("firstAirDate") or ""
        out.append(
            {
                "title": r.get("title") or r.get("name"),
                "mediaType": r.get("mediaType"),
                "tmdbId": r.get("id"),
                "year": date[:4] if date else None,
                "status": (r.get("mediaInfo") or {}).get("status"),
            }
        )
    return {"count": len(out), "results": out}


@mcp.tool()
async def jellyseerr_issues(limit: int = 20) -> Any:
    """List reported issues in Jellyseerr (READ-ONLY)."""
    return await _require(config.JELLYSEERR, "Jellyseerr").get(
        "issue", params={"take": limit, "sort": "added"}
    )


@mcp.tool()
async def jellyseerr_delete_request(request_id: int, confirm: bool = False) -> dict:
    """Delete a media request from Jellyseerr.

    GUARDED WRITE. With confirm=False (default) deletes NOTHING — returns a preview.
    Re-call with confirm=True to DELETE request/{id}.
    """
    client = _require(config.JELLYSEERR, "Jellyseerr")
    preview = {"requestId": request_id}
    if not confirm:
        preview["action"] = "preview"
        preview["note"] = "Nothing deleted. Re-call with confirm=True to delete this request."
        return preview
    status = await client.delete(f"request/{request_id}")
    return {**preview, "action": "deleted", "deleteHttpStatus": status}
