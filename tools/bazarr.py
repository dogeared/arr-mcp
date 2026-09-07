"""Bazarr (Subtitles) tools.

NOTE: Bazarr's API is finicky — several write endpoints expect FORM-encoded
bodies (not JSON), handled via Arr.post_form. The subtitle-download tools here
have not been exercised against a live Bazarr and should be smoke-tested.
"""
from __future__ import annotations

from typing import Any, Optional

from mcp_instance import mcp

import config
from tools.common import _require


# ============================================================================
# BAZARR (Subtitles) — existing tools
# ============================================================================
@mcp.tool()
async def bazarr_status() -> Any:
    """Bazarr system status (reachability + version)."""
    return await _require(config.BAZARR, "Bazarr").get("system/status")


# ============================================================================
# BAZARR (Subtitles) — new management tools
# ============================================================================
@mcp.tool()
async def bazarr_wanted_series(limit: int = 100) -> dict:
    """List episodes with wanted (missing) subtitles (READ-ONLY).

    Returns rows: seriesTitle, season, episode, episodeTitle, missing_subtitles,
    sonarrSeriesId, sonarrEpisodeId. Feed sonarrEpisodeId to
    bazarr_episode_subtitle_search / bazarr_download_episode_subtitle.
    """
    client = _require(config.BAZARR, "Bazarr")
    data = await client.get("episodes/wanted", params={"start": 0, "length": limit})
    rows = data.get("data", data if isinstance(data, list) else [])
    out = []
    for r in rows:
        out.append(
            {
                "seriesTitle": r.get("seriesTitle"),
                "season": r.get("season"),
                "episode": r.get("episode"),
                "episodeTitle": r.get("episodeTitle") or r.get("episode_title"),
                "missing_subtitles": r.get("missing_subtitles"),
                "sonarrSeriesId": r.get("sonarrSeriesId"),
                "sonarrEpisodeId": r.get("sonarrEpisodeId"),
            }
        )
    return {"count": len(out), "wanted": out}


@mcp.tool()
async def bazarr_wanted_movies(limit: int = 100) -> dict:
    """List movies with wanted (missing) subtitles (READ-ONLY).

    Returns rows: title, missing_subtitles, radarrId. Feed radarrId to
    bazarr_movie_subtitle_search / bazarr_download_movie_subtitle.
    """
    client = _require(config.BAZARR, "Bazarr")
    data = await client.get("movies/wanted", params={"start": 0, "length": limit})
    rows = data.get("data", data if isinstance(data, list) else [])
    out = []
    for r in rows:
        out.append(
            {
                "title": r.get("title"),
                "missing_subtitles": r.get("missing_subtitles"),
                "radarrId": r.get("radarrId"),
            }
        )
    return {"count": len(out), "wanted": out}


@mcp.tool()
async def bazarr_movie_subtitle_search(radarr_id: int) -> Any:
    """Search providers for available subtitles for a movie (READ-ONLY).

    Returns candidate subtitles; each carries the `subtitle`, `provider`,
    `original_format`, `hi` and `forced` values needed by
    bazarr_download_movie_subtitle.
    """
    return await _require(config.BAZARR, "Bazarr").get(
        "providers/movies", params={"radarrid": radarr_id}
    )


@mcp.tool()
async def bazarr_episode_subtitle_search(sonarr_episode_id: int) -> Any:
    """Search providers for available subtitles for an episode (READ-ONLY).

    Returns candidate subtitles; each carries the `subtitle`, `provider`,
    `original_format`, `hi` and `forced` values needed by
    bazarr_download_episode_subtitle.
    """
    return await _require(config.BAZARR, "Bazarr").get(
        "providers/episodes", params={"episodeid": sonarr_episode_id}
    )


@mcp.tool()
async def bazarr_download_movie_subtitle(
    radarr_id: int,
    language: str,
    hi: bool,
    forced: bool,
    provider: str,
    subtitle: str,
    original_format: bool,
    confirm: bool = False,
) -> dict:
    """Download a SPECIFIC movie subtitle chosen from bazarr_movie_subtitle_search.

    GUARDED WRITE (FORM-encoded POST providers/movies). With confirm=False (default)
    downloads NOTHING — returns a preview of the parameters. Re-call with
    confirm=True to download. Pass provider/subtitle/original_format exactly as
    returned by the search. NEEDS SMOKE TEST against a live Bazarr.
    """
    client = _require(config.BAZARR, "Bazarr")
    form = {
        "radarrid": radarr_id,
        "language": language,
        "hi": hi,
        "forced": forced,
        "provider": provider,
        "subtitle": subtitle,
        "original_format": original_format,
    }
    if not confirm:
        return {
            "action": "preview",
            "note": "Nothing downloaded. Re-call with confirm=True to download this subtitle.",
            "params": form,
        }
    r = await client.post_form("providers/movies", data=form)
    return {"action": "downloaded", "params": form, "result": r}


@mcp.tool()
async def bazarr_download_episode_subtitle(
    sonarr_episode_id: int,
    language: str,
    hi: bool,
    forced: bool,
    provider: str,
    subtitle: str,
    original_format: bool,
    confirm: bool = False,
) -> dict:
    """Download a SPECIFIC episode subtitle chosen from bazarr_episode_subtitle_search.

    GUARDED WRITE (FORM-encoded POST providers/episodes). With confirm=False
    (default) downloads NOTHING — returns a preview of the parameters. Re-call with
    confirm=True to download. Pass provider/subtitle/original_format exactly as
    returned by the search. NEEDS SMOKE TEST against a live Bazarr.
    """
    client = _require(config.BAZARR, "Bazarr")
    form = {
        "episodeid": sonarr_episode_id,
        "language": language,
        "hi": hi,
        "forced": forced,
        "provider": provider,
        "subtitle": subtitle,
        "original_format": original_format,
    }
    if not confirm:
        return {
            "action": "preview",
            "note": "Nothing downloaded. Re-call with confirm=True to download this subtitle.",
            "params": form,
        }
    r = await client.post_form("providers/episodes", data=form)
    return {"action": "downloaded", "params": form, "result": r}
