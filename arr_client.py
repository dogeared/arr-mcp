"""Thin async HTTP client for *arr-family REST APIs.

One class covers Sonarr, Radarr, Prowlarr, Jellyseerr and Bazarr — they only
differ by API version prefix and the name of the API-key header. Instances are
created from environment variables in config.py and are `None` when a service
isn't configured, so tools can degrade gracefully.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

# Per-request timeout. Searches/commands return immediately (they queue), so a
# short-ish timeout is fine; large list endpoints (episodes) are still quick.
DEFAULT_TIMEOUT = 30.0


class Arr:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_prefix: str = "api/v3",
        key_header: str = "X-Api-Key",
        name: str = "service",
    ) -> None:
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.api_prefix = api_prefix.strip("/")
        self.key_header = key_header
        self.name = name

    def _headers(self) -> dict[str, str]:
        return {self.key_header: self.key, "Content-Type": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.base}/{self.api_prefix}/{path.lstrip('/')}"

    async def get(self, path: str, params: Optional[dict] = None) -> Any:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(self._url(path), headers=self._headers(), params=params)
            r.raise_for_status()
            return r.json()

    async def post(self, path: str, json: Optional[dict] = None) -> Any:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.post(self._url(path), headers=self._headers(), json=json or {})
            r.raise_for_status()
            return r.json() if r.content else {}

    async def delete(
        self, path: str, json: Optional[dict] = None, params: Optional[dict] = None
    ) -> int:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.request(
                "DELETE",
                self._url(path),
                headers=self._headers(),
                json=json,
                params=params,
            )
            r.raise_for_status()
            return r.status_code

    async def ping(self) -> bool:
        """Cheap reachability check via the service's system status endpoint."""
        try:
            await self.get("system/status")
            return True
        except Exception:
            return False
