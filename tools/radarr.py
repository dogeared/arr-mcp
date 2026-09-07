"""Radarr (Movies) tools."""
from __future__ import annotations

from typing import Any, Optional

from mcp_instance import mcp

import config
from tools.common import _brief_queue_item, _brief_release, _is_stuck, _require


# ============================================================================
# RADARR (Movies) — existing tools
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


@mcp.tool()
async def radarr_interactive_search(
    movie_id: int, quality: Optional[str] = None, limit: int = 30
) -> dict:
    """Interactive/manual release search for a movie (READ-ONLY).

    Returns candidate releases in Radarr's ranked order (best first) with quality,
    size, protocol, seeders, indexer, custom-format score, and any rejection reasons
    — plus each release's `guid` and `indexerId` to hand to `radarr_grab_release`.
    `quality` filters by substring on the quality name (e.g. "1080p", "2160p",
    "Bluray"). Use this to pick a SPECIFIC release instead of letting the profile
    auto-grab — e.g. choosing a 1080p SDR over a Dolby-Vision WEB-DL.
    """
    client = _require(config.RADARR, "Radarr")
    data = await client.get("release", params={"movieId": movie_id})
    rels = [_brief_release(r) for r in data]
    if quality:
        ql = quality.lower()
        rels = [r for r in rels if ql in (r["quality"] or "").lower()]
    return {"count": len(rels), "releases": rels[:limit]}


@mcp.tool()
async def radarr_grab_release(
    guid: str, indexer_id: int, confirm: bool = False, title: Optional[str] = None
) -> dict:
    """Grab (send to the download client) a SPECIFIC release from radarr_interactive_search.

    GUARDED WRITE. With `confirm=False` (default) it grabs NOTHING — it echoes back
    the guid/indexerId (and optional title) it would push, so you can eyeball the pick.
    Re-call with `confirm=True` to actually send it to SAB/qBit. Pass `guid` and
    `indexer_id` exactly as returned by the search. This overrides the quality
    profile's automatic choice (e.g. force a 1080p when the profile prefers 2160p).
    NOTE: Radarr's grab response status is unreliable — verify with radarr_queue.
    """
    client = _require(config.RADARR, "Radarr")
    if not confirm:
        return {
            "action": "preview",
            "note": "Nothing grabbed. Re-call with confirm=True to send this release.",
            "guid": guid,
            "indexerId": indexer_id,
            "title": title,
        }
    r = await client.post("release", json={"guid": guid, "indexerId": indexer_id})
    return {
        "action": "grabbed",
        "guid": guid,
        "indexerId": indexer_id,
        "title": title,
        "result": r if isinstance(r, dict) else {"ok": True},
        "verify": "Check radarr_queue to confirm it landed.",
    }


# ============================================================================
# RADARR (Movies) — new management tools
# ============================================================================
@mcp.tool()
async def radarr_quality_profiles() -> list[dict]:
    """List Radarr quality profiles (READ-ONLY) as [{id, name}]."""
    data = await _require(config.RADARR, "Radarr").get("qualityprofile")
    return [{"id": p.get("id"), "name": p.get("name")} for p in data]


@mcp.tool()
async def radarr_root_folders() -> list[dict]:
    """List Radarr root folders (READ-ONLY) as [{id, path, freeSpace}]."""
    data = await _require(config.RADARR, "Radarr").get("rootfolder")
    return [
        {"id": f.get("id"), "path": f.get("path"), "freeSpace": f.get("freeSpace")}
        for f in data
    ]


@mcp.tool()
async def radarr_tags() -> list[dict]:
    """List Radarr tags (READ-ONLY) as [{id, label}]."""
    data = await _require(config.RADARR, "Radarr").get("tag")
    return [{"id": t.get("id"), "label": t.get("label")} for t in data]


@mcp.tool()
async def radarr_add_tag(label: str) -> dict:
    """Create a new Radarr tag. WRITE ACTION. Returns the created {id, label}."""
    r = await _require(config.RADARR, "Radarr").post("tag", json={"label": label})
    return {"id": r.get("id"), "label": r.get("label")}


@mcp.tool()
async def radarr_movie_detail(movie_id: int) -> dict:
    """Full detail for one movie (READ-ONLY): title, year, monitored, hasFile,
    path, current quality and on-disk size."""
    m = await _require(config.RADARR, "Radarr").get(f"movie/{movie_id}")
    mf = m.get("movieFile") or {}
    return {
        "id": m.get("id"),
        "title": m.get("title"),
        "year": m.get("year"),
        "monitored": m.get("monitored"),
        "hasFile": m.get("hasFile"),
        "path": m.get("path"),
        "qualityProfileId": m.get("qualityProfileId"),
        "quality": (((mf.get("quality") or {}).get("quality")) or {}).get("name"),
        "sizeGB": round((mf.get("size") or 0) / (1024 ** 3), 2) if mf.get("size") else None,
    }


@mcp.tool()
async def radarr_edit_movie(
    movie_id: int,
    monitored: Optional[bool] = None,
    quality_profile_id: Optional[int] = None,
    confirm: bool = False,
) -> dict:
    """Edit movie-level settings (monitored on/off, quality profile).

    GUARDED WRITE. Only the provided fields change. With confirm=False (default)
    changes NOTHING — returns a before/after preview. Re-call with confirm=True to
    apply via PUT movie/{id}.
    """
    client = _require(config.RADARR, "Radarr")
    m = await client.get(f"movie/{movie_id}")
    changes = {}
    if monitored is not None and monitored != m.get("monitored"):
        changes["monitored"] = {"from": m.get("monitored"), "to": monitored}
    if quality_profile_id is not None and quality_profile_id != m.get("qualityProfileId"):
        changes["qualityProfileId"] = {"from": m.get("qualityProfileId"), "to": quality_profile_id}
    preview = {"movieId": movie_id, "title": m.get("title"), "changes": changes}
    if not confirm:
        preview["action"] = "preview"
        preview["note"] = "Nothing changed. Re-call with confirm=True to apply."
        return preview
    if monitored is not None:
        m["monitored"] = monitored
    if quality_profile_id is not None:
        m["qualityProfileId"] = quality_profile_id
    updated = await client.put(f"movie/{movie_id}", json=m)
    body = updated if isinstance(updated, dict) and updated else m
    return {
        "action": "updated",
        "movieId": movie_id,
        "title": body.get("title"),
        "monitored": body.get("monitored"),
        "qualityProfileId": body.get("qualityProfileId"),
    }


@mcp.tool()
async def radarr_add_movie(
    tmdb_id: int,
    quality_profile_id: int,
    root_folder_path: str,
    monitored: bool = True,
    search_for_movie: bool = False,
    confirm: bool = False,
) -> dict:
    """Add a new movie to Radarr by TMDB id.

    GUARDED WRITE. Looks the movie up via movie/lookup?term=tmdb:{id}, fills in
    qualityProfileId / rootFolderPath / monitored / addOptions, then (with
    confirm=True) POSTs it. With confirm=False (default) adds NOTHING and returns a
    preview. `search_for_movie=True` kicks off an immediate search after adding.
    """
    client = _require(config.RADARR, "Radarr")
    matches = await client.get("movie/lookup", params={"term": f"tmdb:{tmdb_id}"})
    if not matches:
        return {"action": "none", "reason": f"No movie found for tmdb:{tmdb_id}."}
    payload = matches[0] if isinstance(matches, list) else matches
    payload["qualityProfileId"] = quality_profile_id
    payload["rootFolderPath"] = root_folder_path
    payload["monitored"] = monitored
    payload["addOptions"] = {"searchForMovie": search_for_movie}
    preview = {
        "tmdbId": tmdb_id,
        "title": payload.get("title"),
        "year": payload.get("year"),
        "qualityProfileId": quality_profile_id,
        "rootFolderPath": root_folder_path,
        "monitored": monitored,
        "searchForMovie": search_for_movie,
    }
    if not confirm:
        preview["action"] = "preview"
        preview["note"] = "Nothing added. Re-call with confirm=True to add this movie."
        return preview
    r = await client.post("movie", json=payload)
    return {"action": "added", "id": r.get("id"), "title": r.get("title"), "year": r.get("year")}


@mcp.tool()
async def radarr_delete_movie(
    movie_id: int, delete_files: bool = False, confirm: bool = False
) -> dict:
    """Remove a movie from Radarr, optionally deleting its file.

    GUARDED WRITE. With confirm=False (default) deletes NOTHING — returns a preview
    (title, path, hasFile, whether files would be deleted). Re-call with
    confirm=True to DELETE movie/{id}?deleteFiles={bool}.
    """
    client = _require(config.RADARR, "Radarr")
    m = await client.get(f"movie/{movie_id}")
    preview = {
        "movieId": movie_id,
        "title": f"{m.get('title')} ({m.get('year')})",
        "path": m.get("path"),
        "hasFile": m.get("hasFile"),
        "deleteFiles": delete_files,
    }
    if not confirm:
        preview["action"] = "preview"
        preview["note"] = "Nothing deleted. Re-call with confirm=True to remove this movie."
        return preview
    status = await client.delete(
        f"movie/{movie_id}", params={"deleteFiles": str(delete_files).lower()}
    )
    return {**preview, "action": "deleted", "deleteHttpStatus": status}


@mcp.tool()
async def radarr_queue_remove(
    queue_ids: list[int],
    remove_from_client: bool = True,
    blocklist: bool = True,
    confirm: bool = False,
) -> dict:
    """Remove items from the Radarr download queue by queue id.

    GUARDED WRITE. With confirm=False (default) removes NOTHING — returns a preview
    of the matched queue items. Re-call with confirm=True to remove them via
    queue/bulk (removeFromClient / blocklist query params control whether the
    download is also deleted from the client and the release blocklisted).
    """
    client = _require(config.RADARR, "Radarr")
    data = await client.get("queue", params={"pageSize": 250, "includeMovie": "true"})
    records = data.get("records", data if isinstance(data, list) else [])
    matched = [_brief_queue_item(x) for x in records if x.get("id") in queue_ids]
    preview = {
        "queueIds": queue_ids,
        "matched": len(matched),
        "items": matched,
        "removeFromClient": remove_from_client,
        "blocklist": blocklist,
    }
    if not confirm:
        preview["action"] = "preview"
        preview["note"] = "Nothing removed. Re-call with confirm=True to remove these items."
        return preview
    status = await client.delete(
        "queue/bulk",
        json={"ids": queue_ids},
        params={
            "removeFromClient": str(remove_from_client).lower(),
            "blocklist": str(blocklist).lower(),
        },
    )
    return {**preview, "action": "removed", "deleteHttpStatus": status}


@mcp.tool()
async def radarr_history(movie_id: Optional[int] = None, limit: int = 30) -> dict:
    """Recent Radarr history (grabs, imports, deletions). READ-ONLY.

    Without `movie_id` returns global recent history; with it, history scoped to
    that movie. Returns brief rows: date, eventType, sourceTitle, quality, movieId.
    """
    client = _require(config.RADARR, "Radarr")
    if movie_id is not None:
        data = await client.get("history/movie", params={"movieId": movie_id})
        records = data if isinstance(data, list) else data.get("records", [])
    else:
        data = await client.get("history", params={"pageSize": limit, "sortKey": "date", "sortDirection": "descending"})
        records = data.get("records", data if isinstance(data, list) else [])
    out = []
    for h in records[:limit]:
        q = (((h.get("quality") or {}).get("quality")) or {}).get("name")
        out.append(
            {
                "date": h.get("date"),
                "eventType": h.get("eventType"),
                "sourceTitle": h.get("sourceTitle"),
                "quality": q,
                "movieId": h.get("movieId"),
            }
        )
    return {"count": len(out), "history": out}


@mcp.tool()
async def radarr_custom_formats() -> list[dict]:
    """List Radarr custom formats (READ-ONLY). Radarr uses custom formats (not
    release profiles) to score/prefer or reject releases."""
    return await _require(config.RADARR, "Radarr").get("customformat")
