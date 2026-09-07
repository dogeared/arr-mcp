"""Sonarr (TV) tools."""
from __future__ import annotations

from typing import Any, Optional

from mcp_instance import mcp

import config
from tools.common import _brief_queue_item, _brief_release, _brief_series, _is_stuck, _require


# ============================================================================
# SONARR (TV) — existing tools
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


@mcp.tool()
async def sonarr_episode_files(series_id: int, season: Optional[int] = None) -> dict:
    """Per-episode file inventory for a series (READ-ONLY). Optionally one `season`.

    For each episode returns: epId, season, episode, title, monitored, hasFile, and
    (if present) episodeFileId, the current on-disk `file` path, `quality`,
    `releaseGroup`, and `sceneName` (the ORIGINAL release title Sonarr imported —
    the reliable tell for US 'Baking Show'/Netflix vs UK 'Bake Off'/BBC, since
    Sonarr renames the on-disk file to the series title on import). Feed
    episodeFileId to sonarr_delete_episode_files and epId to
    sonarr_monitor_episodes / sonarr_search_episodes.
    """
    client = _require(config.SONARR, "Sonarr")
    params = {"seriesId": series_id, "includeEpisodeFile": "true"}
    eps = await client.get("episode", params=params)
    out = []
    for e in eps:
        if season is not None and e.get("seasonNumber") != season:
            continue
        ef = e.get("episodeFile") or {}
        q = (((ef.get("quality") or {}).get("quality")) or {}).get("name")
        out.append(
            {
                "epId": e.get("id"),
                "season": e.get("seasonNumber"),
                "episode": e.get("episodeNumber"),
                "title": e.get("title"),
                "monitored": e.get("monitored"),
                "hasFile": e.get("hasFile"),
                "episodeFileId": ef.get("id"),
                "file": ef.get("relativePath"),
                "sceneName": ef.get("sceneName"),
                "releaseGroup": ef.get("releaseGroup"),
                "quality": q,
                "sizeGB": round((ef.get("size") or 0) / (1024 ** 3), 2) if ef.get("size") else None,
            }
        )
    out.sort(key=lambda x: (x["season"] if x["season"] is not None else -1, x["episode"] or 0))
    return {"seriesId": series_id, "count": len(out), "episodes": out}


@mcp.tool()
async def sonarr_delete_episode_files(
    episode_file_ids: list[int], confirm: bool = False
) -> dict:
    """Delete specific episode FILES by episodeFileId (from sonarr_episode_files).

    GUARDED WRITE. With confirm=False (default) deletes NOTHING — returns a preview
    of exactly which files would go (path, quality, size, releaseGroup). Re-call
    with confirm=True to delete. Only ever touches the explicit ids passed. Each
    deleted file leaves its episode monitored-and-missing so it can be re-grabbed.
    """
    client = _require(config.SONARR, "Sonarr")
    preview = []
    for fid in episode_file_ids:
        try:
            ef = await client.get(f"episodefile/{fid}")
            preview.append(
                {
                    "episodeFileId": fid,
                    "path": ef.get("relativePath") or ef.get("path"),
                    "quality": (((ef.get("quality") or {}).get("quality")) or {}).get("name"),
                    "sceneName": ef.get("sceneName"),
                    "releaseGroup": ef.get("releaseGroup"),
                    "sizeGB": round((ef.get("size") or 0) / (1024 ** 3), 2),
                }
            )
        except Exception as e:  # noqa: BLE001
            preview.append({"episodeFileId": fid, "error": str(e)})
    if not confirm:
        return {
            "action": "preview",
            "count": len(preview),
            "note": "Nothing deleted. Re-call with confirm=True to delete these files.",
            "files": preview,
        }
    status = await client.delete("episodefile/bulk", json={"episodeFileIds": episode_file_ids})
    return {"action": "deleted", "count": len(episode_file_ids), "deleteHttpStatus": status, "files": preview}


@mcp.tool()
async def sonarr_monitor_episodes(episode_ids: list[int], monitored: bool) -> dict:
    """(Un)monitor specific episodes by episode id (`epId` from sonarr_episode_files).

    WRITE ACTION, reversible. Use to unmonitor unwanted episodes (e.g. Season-0
    specials so they stop showing as missing) or ensure slots are monitored before
    a re-search.
    """
    client = _require(config.SONARR, "Sonarr")
    await client.put("episode/monitor", json={"episodeIds": episode_ids, "monitored": monitored})
    return {"updated": len(episode_ids), "monitored": monitored}


# ============================================================================
# SONARR (TV) — new management tools
# ============================================================================
@mcp.tool()
async def sonarr_quality_profiles() -> list[dict]:
    """List Sonarr quality profiles (READ-ONLY) as [{id, name}]. Use the id when
    adding or editing a series."""
    data = await _require(config.SONARR, "Sonarr").get("qualityprofile")
    return [{"id": p.get("id"), "name": p.get("name")} for p in data]


@mcp.tool()
async def sonarr_root_folders() -> list[dict]:
    """List Sonarr root folders (READ-ONLY) as [{id, path, freeSpace}]. Use a path
    when adding a series."""
    data = await _require(config.SONARR, "Sonarr").get("rootfolder")
    return [
        {"id": f.get("id"), "path": f.get("path"), "freeSpace": f.get("freeSpace")}
        for f in data
    ]


@mcp.tool()
async def sonarr_tags() -> list[dict]:
    """List Sonarr tags (READ-ONLY) as [{id, label}]."""
    data = await _require(config.SONARR, "Sonarr").get("tag")
    return [{"id": t.get("id"), "label": t.get("label")} for t in data]


@mcp.tool()
async def sonarr_add_tag(label: str) -> dict:
    """Create a new Sonarr tag. WRITE ACTION. Returns the created {id, label}."""
    r = await _require(config.SONARR, "Sonarr").post("tag", json={"label": label})
    return {"id": r.get("id"), "label": r.get("label")}


@mcp.tool()
async def sonarr_edit_series(
    series_id: int,
    monitored: Optional[bool] = None,
    quality_profile_id: Optional[int] = None,
    series_type: Optional[str] = None,
    confirm: bool = False,
) -> dict:
    """Edit series-level settings (monitored on/off, quality profile, series type).

    GUARDED WRITE. Only the provided fields change; omitted fields are left as-is.
    With confirm=False (default) changes NOTHING — returns a before/after preview.
    Re-call with confirm=True to apply via PUT series/{id}. `series_type` is one of
    standard, daily, anime.
    """
    client = _require(config.SONARR, "Sonarr")
    s = await client.get(f"series/{series_id}")
    changes = {}
    if monitored is not None and monitored != s.get("monitored"):
        changes["monitored"] = {"from": s.get("monitored"), "to": monitored}
    if quality_profile_id is not None and quality_profile_id != s.get("qualityProfileId"):
        changes["qualityProfileId"] = {"from": s.get("qualityProfileId"), "to": quality_profile_id}
    if series_type is not None and series_type != s.get("seriesType"):
        changes["seriesType"] = {"from": s.get("seriesType"), "to": series_type}
    preview = {
        "seriesId": series_id,
        "title": s.get("title"),
        "changes": changes,
    }
    if not confirm:
        preview["action"] = "preview"
        preview["note"] = "Nothing changed. Re-call with confirm=True to apply."
        return preview
    if monitored is not None:
        s["monitored"] = monitored
    if quality_profile_id is not None:
        s["qualityProfileId"] = quality_profile_id
    if series_type is not None:
        s["seriesType"] = series_type
    updated = await client.put(f"series/{series_id}", json=s)
    body = updated if isinstance(updated, dict) and updated else s
    return {
        "action": "updated",
        "seriesId": series_id,
        "title": body.get("title"),
        "monitored": body.get("monitored"),
        "qualityProfileId": body.get("qualityProfileId"),
        "seriesType": body.get("seriesType"),
    }


@mcp.tool()
async def sonarr_add_series(
    tvdb_id: int,
    quality_profile_id: int,
    root_folder_path: str,
    monitored: bool = True,
    season_folder: bool = True,
    search_for_missing: bool = False,
    confirm: bool = False,
) -> dict:
    """Add a new series to Sonarr by TVDB id.

    GUARDED WRITE. Looks the series up via series/lookup?term=tvdb:{id}, fills in
    qualityProfileId / rootFolderPath / monitored / seasonFolder / addOptions, then
    (with confirm=True) POSTs it. With confirm=False (default) adds NOTHING and
    returns a preview of the series that would be added. `search_for_missing=True`
    kicks off an immediate search after adding.
    """
    client = _require(config.SONARR, "Sonarr")
    matches = await client.get("series/lookup", params={"term": f"tvdb:{tvdb_id}"})
    if not matches:
        return {"action": "none", "reason": f"No series found for tvdb:{tvdb_id}."}
    payload = matches[0]
    payload["qualityProfileId"] = quality_profile_id
    payload["rootFolderPath"] = root_folder_path
    payload["monitored"] = monitored
    payload["seasonFolder"] = season_folder
    payload["addOptions"] = {"searchForMissingEpisodes": search_for_missing}
    preview = {
        "tvdbId": tvdb_id,
        "title": payload.get("title"),
        "year": payload.get("year"),
        "qualityProfileId": quality_profile_id,
        "rootFolderPath": root_folder_path,
        "monitored": monitored,
        "seasonFolder": season_folder,
        "searchForMissing": search_for_missing,
    }
    if not confirm:
        preview["action"] = "preview"
        preview["note"] = "Nothing added. Re-call with confirm=True to add this series."
        return preview
    r = await client.post("series", json=payload)
    return {
        "action": "added",
        "id": r.get("id"),
        "title": r.get("title"),
        "year": r.get("year"),
    }


@mcp.tool()
async def sonarr_delete_series(
    series_id: int, delete_files: bool = False, confirm: bool = False
) -> dict:
    """Remove a series from Sonarr, optionally deleting its files.

    GUARDED WRITE. With confirm=False (default) deletes NOTHING — returns a preview
    (title, path, episode-file count, whether files would be deleted). Re-call with
    confirm=True to DELETE series/{id}?deleteFiles={bool}. `delete_files=True` also
    removes the on-disk files (irreversible for those files).
    """
    client = _require(config.SONARR, "Sonarr")
    s = await client.get(f"series/{series_id}")
    st = s.get("statistics", {}) or {}
    preview = {
        "seriesId": series_id,
        "title": s.get("title"),
        "path": s.get("path"),
        "episodeFileCount": st.get("episodeFileCount"),
        "deleteFiles": delete_files,
    }
    if not confirm:
        preview["action"] = "preview"
        preview["note"] = "Nothing deleted. Re-call with confirm=True to remove this series."
        return preview
    status = await client.delete(
        f"series/{series_id}", params={"deleteFiles": str(delete_files).lower()}
    )
    return {**preview, "action": "deleted", "deleteHttpStatus": status}


@mcp.tool()
async def sonarr_monitor_season(
    series_id: int, season_number: int, monitored: bool
) -> dict:
    """(Un)monitor a whole SEASON of a series.

    WRITE ACTION, reversible. Flips the season's `monitored` flag in the series'
    seasons array and PUTs the series back. Use to stop tracking (or start
    tracking) an entire season at once.
    """
    client = _require(config.SONARR, "Sonarr")
    s = await client.get(f"series/{series_id}")
    found = False
    for se in s.get("seasons", []):
        if se.get("seasonNumber") == season_number:
            se["monitored"] = monitored
            found = True
    if not found:
        return {
            "action": "none",
            "reason": f"Season {season_number} not found on series {series_id}.",
        }
    await client.put(f"series/{series_id}", json=s)
    return {
        "action": "updated",
        "seriesId": series_id,
        "season": season_number,
        "monitored": monitored,
    }


@mcp.tool()
async def sonarr_interactive_search(episode_id: int, limit: int = 30) -> dict:
    """Interactive/manual release search for one episode (READ-ONLY).

    Returns candidate releases in Sonarr's ranked order (best first) with quality,
    size, protocol, seeders, indexer, custom-format score, and any rejection reasons
    — plus each release's `guid` and `indexerId` to hand to `sonarr_grab_release`.
    """
    client = _require(config.SONARR, "Sonarr")
    data = await client.get("release", params={"episodeId": episode_id})
    rels = [_brief_release(r) for r in data]
    return {"count": len(rels), "releases": rels[:limit]}


@mcp.tool()
async def sonarr_grab_release(
    guid: str, indexer_id: int, confirm: bool = False, title: Optional[str] = None
) -> dict:
    """Grab (send to the download client) a SPECIFIC release from sonarr_interactive_search.

    GUARDED WRITE. With confirm=False (default) it grabs NOTHING — it echoes back
    the guid/indexerId (and optional title) it would push. Re-call with confirm=True
    to actually send it. Pass `guid` and `indexer_id` exactly as returned by the
    search. NOTE: Sonarr's grab response status is unreliable — verify with
    sonarr_queue.
    """
    client = _require(config.SONARR, "Sonarr")
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
        "verify": "Check sonarr_queue to confirm it landed.",
    }


@mcp.tool()
async def sonarr_queue_remove(
    queue_ids: list[int],
    remove_from_client: bool = True,
    blocklist: bool = True,
    confirm: bool = False,
) -> dict:
    """Remove items from the Sonarr download queue by queue id.

    GUARDED WRITE. With confirm=False (default) removes NOTHING — returns a preview
    of the matched queue items. Re-call with confirm=True to remove them via
    queue/bulk (removeFromClient / blocklist query params control whether the
    download is also deleted from the client and the release blocklisted).
    """
    client = _require(config.SONARR, "Sonarr")
    data = await client.get("queue", params={"pageSize": 250, "includeSeries": "true"})
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
async def sonarr_history(series_id: Optional[int] = None, limit: int = 30) -> dict:
    """Recent Sonarr history (grabs, imports, deletions). READ-ONLY.

    Without `series_id` returns global recent history; with it, history scoped to
    that series. Returns brief rows: date, eventType, sourceTitle, quality, episode.
    """
    client = _require(config.SONARR, "Sonarr")
    if series_id is not None:
        data = await client.get("history/series", params={"seriesId": series_id})
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
                "episodeId": h.get("episodeId"),
            }
        )
    return {"count": len(out), "history": out}


@mcp.tool()
async def sonarr_release_profiles() -> list[dict]:
    """List Sonarr release profiles (READ-ONLY). Release profiles hold
    must-contain (`required`) and must-not-contain (`ignored`) term lists."""
    return await _require(config.SONARR, "Sonarr").get("releaseprofile")


@mcp.tool()
async def sonarr_add_release_profile(
    must_not_contain: list[str] = [],
    must_contain: list[str] = [],
    name: Optional[str] = None,
    tag_ids: list[int] = [],
    confirm: bool = False,
) -> dict:
    """Create a Sonarr release profile (must-contain / must-not-contain terms).

    GUARDED WRITE. With confirm=False (default) creates NOTHING — returns a preview
    of the profile that would be created. Re-call with confirm=True to POST it.
    Maps to Sonarr's fields: `required` = must_contain, `ignored` = must_not_contain,
    `tags` = tag_ids. Use to (e.g.) block a release group or require a term.
    """
    client = _require(config.SONARR, "Sonarr")
    payload = {
        "name": name,
        "required": must_contain,
        "ignored": must_not_contain,
        "tags": tag_ids,
        "enabled": True,
        "indexerId": 0,
    }
    preview = {
        "name": name,
        "required": must_contain,
        "ignored": must_not_contain,
        "tags": tag_ids,
    }
    if not confirm:
        preview["action"] = "preview"
        preview["note"] = "Nothing created. Re-call with confirm=True to add this release profile."
        return preview
    r = await client.post("releaseprofile", json=payload)
    return {"action": "created", "id": r.get("id"), "profile": r}
