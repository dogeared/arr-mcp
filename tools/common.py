"""Shared helpers and the cross-service health overview tool.

Importing this module registers `stack_health` on the shared FastMCP instance.
The `_*` helpers here are imported by the per-service tool modules.
"""
from __future__ import annotations

from typing import Any, Optional

from mcp_instance import mcp

import config


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


def _brief_release(r: dict) -> dict:
    q = ((r.get("quality") or {}).get("quality")) or {}
    return {
        "title": r.get("title"),
        "quality": q.get("name"),
        "resolution": q.get("resolution"),
        "sizeGB": round((r.get("size") or 0) / (1024 ** 3), 2),
        "protocol": r.get("protocol"),
        "seeders": r.get("seeders"),
        "indexer": r.get("indexer"),
        "cfScore": r.get("customFormatScore"),
        "rejected": bool(r.get("rejected")),
        "rejections": (r.get("rejections") or [])[:3],
        "guid": r.get("guid"),
        "indexerId": r.get("indexerId"),
    }


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
