"""Configuration loaded from environment variables.

Each service is optional: if its URL and API key are both present, a client is
built; otherwise it's `None` and the corresponding tools return a clear
"not configured" message instead of crashing.
"""
from __future__ import annotations

import os
from typing import Optional

from arr_client import Arr


def _client(
    url_var: str,
    key_var: str,
    api_prefix: str = "api/v3",
    key_header: str = "X-Api-Key",
    name: str = "",
) -> Optional[Arr]:
    url = os.getenv(url_var, "").strip()
    key = os.getenv(key_var, "").strip()
    if not url or not key:
        return None
    return Arr(url, key, api_prefix=api_prefix, key_header=key_header, name=name or url_var)


# --- Services ---------------------------------------------------------------
# Sonarr / Radarr / Prowlarr use the standard *arr scheme (X-Api-Key header).
# Jellyseerr uses X-Api-Key on /api/v1. Bazarr uses X-API-KEY on /api.
SONARR = _client("SONARR_URL", "SONARR_API_KEY", "api/v3", name="Sonarr")
RADARR = _client("RADARR_URL", "RADARR_API_KEY", "api/v3", name="Radarr")
PROWLARR = _client("PROWLARR_URL", "PROWLARR_API_KEY", "api/v1", name="Prowlarr")
JELLYSEERR = _client("JELLYSEERR_URL", "JELLYSEERR_API_KEY", "api/v1", name="Jellyseerr")
BAZARR = _client("BAZARR_URL", "BAZARR_API_KEY", "api", key_header="X-API-KEY", name="Bazarr")

# --- Endpoint auth ----------------------------------------------------------
# Bearer token required on the MCP HTTP endpoint. If empty, auth is disabled
# (only acceptable when the server is bound to localhost / a private network
# with no tunnel in front — never expose it publicly without a token).
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "").strip()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8787"))

ALL_SERVICES = {
    "sonarr": SONARR,
    "radarr": RADARR,
    "prowlarr": PROWLARR,
    "jellyseerr": JELLYSEERR,
    "bazarr": BAZARR,
}
