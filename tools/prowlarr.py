"""Prowlarr (Indexers) tools."""
from __future__ import annotations

from typing import Any, Optional

from mcp_instance import mcp

import config
from tools.common import _require


# ============================================================================
# PROWLARR (Indexers) — existing tools
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


@mcp.tool()
async def prowlarr_test_indexers() -> dict:
    """Test every configured indexer and report which pass vs fail.

    READ-ONLY (runs a live connectivity test; changes no config). Returns each
    indexer's name, whether it passed, and any error messages — use this to see
    WHY an indexer flagged in prowlarr_health is failing (dead domain, Cloudflare
    challenge, auth, etc.) before deciding to fix or disable it.
    """
    client = _require(config.PROWLARR, "Prowlarr")
    idx = await client.get("indexer")
    names = {i.get("id"): i.get("name") for i in idx}
    results = await client.post_raw("indexer/testall", timeout=120.0)
    out = []
    for r in results or []:
        failures = [
            (f.get("errorMessage") or f.get("propertyName") or "").strip()
            for f in (r.get("validationFailures") or [])
        ]
        out.append(
            {
                "id": r.get("id"),
                "name": names.get(r.get("id"), r.get("id")),
                "ok": bool(r.get("isValid")),
                "errors": [f for f in failures if f],
            }
        )
    return {"tested": len(out), "failing": sum(1 for r in out if not r["ok"]), "results": out}


@mcp.tool()
async def prowlarr_set_indexer(indexer_id: int, enable: bool) -> dict:
    """Enable or disable ONE indexer by id (ids from prowlarr_indexers).

    WRITE ACTION, fully reversible — flips just this indexer's `enable` flag via
    Prowlarr's bulk editor. Use to disable a chronically-dead indexer (so it stops
    adding failures/latency to every search) or re-enable one after a fix.
    """
    client = _require(config.PROWLARR, "Prowlarr")
    idx = await client.get(f"indexer/{indexer_id}")            # full resource
    idx["enable"] = enable
    updated = await client.put(f"indexer/{indexer_id}", json=idx)
    body = updated if isinstance(updated, dict) and updated else idx
    return {
        "id": body.get("id"),
        "name": body.get("name"),
        "enable": body.get("enable"),
        "action": "enabled" if enable else "disabled",
    }


# ============================================================================
# PROWLARR (Indexers) — new management tools
# ============================================================================
@mcp.tool()
async def prowlarr_indexer_detail(indexer_id: int) -> dict:
    """Full detail for one indexer (READ-ONLY): id, name, enable, protocol,
    priority, and a brief view of its configured fields (name -> value)."""
    i = await _require(config.PROWLARR, "Prowlarr").get(f"indexer/{indexer_id}")
    fields = {
        f.get("name"): f.get("value")
        for f in (i.get("fields") or [])
        if f.get("name") is not None
    }
    return {
        "id": i.get("id"),
        "name": i.get("name"),
        "enable": i.get("enable"),
        "protocol": i.get("protocol"),
        "priority": i.get("priority"),
        "fields": fields,
    }


@mcp.tool()
async def prowlarr_search(query: str, limit: int = 30) -> dict:
    """Search across all enabled indexers via Prowlarr (READ-ONLY).

    Returns brief release rows (title, indexer, size, seeders, protocol, guid).
    Handy for confirming an indexer actually returns results for a term.
    """
    client = _require(config.PROWLARR, "Prowlarr")
    data = await client.get("search", params={"query": query})
    results = data if isinstance(data, list) else data.get("records", [])
    out = []
    for r in results[:limit]:
        out.append(
            {
                "title": r.get("title"),
                "indexer": r.get("indexer"),
                "sizeGB": round((r.get("size") or 0) / (1024 ** 3), 2),
                "seeders": r.get("seeders"),
                "protocol": r.get("protocol"),
                "guid": r.get("guid"),
            }
        )
    return {"count": len(out), "results": out}


@mcp.tool()
async def prowlarr_delete_indexer(indexer_id: int, confirm: bool = False) -> dict:
    """Delete an indexer from Prowlarr by id.

    GUARDED WRITE. With confirm=False (default) deletes NOTHING — returns a preview
    (id, name). Re-call with confirm=True to DELETE indexer/{id}. Prefer
    prowlarr_set_indexer(enable=False) if you only want to disable it.
    """
    client = _require(config.PROWLARR, "Prowlarr")
    i = await client.get(f"indexer/{indexer_id}")
    preview = {"id": indexer_id, "name": i.get("name")}
    if not confirm:
        preview["action"] = "preview"
        preview["note"] = "Nothing deleted. Re-call with confirm=True to delete this indexer."
        return preview
    status = await client.delete(f"indexer/{indexer_id}")
    return {**preview, "action": "deleted", "deleteHttpStatus": status}
