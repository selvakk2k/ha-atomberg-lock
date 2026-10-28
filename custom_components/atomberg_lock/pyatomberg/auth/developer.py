"""Developer API token authentication handler."""

import time
from typing import Any

import aiohttp

from pyatomberg.const import DEVELOPER_API_BASE
from pyatomberg.exceptions import AtombergAuthError, AtombergConnectionError


class DeveloperTokenHandler:
    """Manages authentication using Atomberg Developer Refresh Tokens.

    Allows credential-free operation for fans by exchanging the refresh token
    obtained from the Atomberg mobile app for short-lived bearer access tokens.
    """

    def __init__(
        self,
        refresh_token: str,
        api_key: str | None = None,
        session: aiohttp.ClientSession | None = None,
        base_url: str = DEVELOPER_API_BASE,
    ) -> None:
        """Initialize the developer token handler."""
        if not refresh_token or not refresh_token.strip():
            raise AtombergAuthError("Developer refresh token cannot be empty")

        self._refresh_token = refresh_token.strip()
        self._api_key = api_key.strip() if api_key else None
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    @property
    def refresh_token(self) -> str:
        """Return the configured refresh token."""
        return self._refresh_token

    @property
    def api_key(self) -> str | None:
        """Return the configured API key, if any."""
        return self._api_key

    @property
    def has_valid_access_token(self) -> bool:
        """Check if cached access token is valid (with 60s safety buffer)."""
        return self._access_token is not None and time.time() < (self._expires_at - 60)

    async def get_access_token(self, force_refresh: bool = False) -> str:
        """Retrieve a valid access token, refreshing if necessary."""
        if not force_refresh and self.has_valid_access_token:
            assert self._access_token is not None
            return self._access_token

        await self.refresh_access_token()
        assert self._access_token is not None
        return self._access_token

    async def refresh_access_token(self) -> str:
        """Exchange developer refresh token for a new access token."""
        url = f"{self._base_url}/get_access_token"
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._refresh_token}",
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key

        close_session = False
        session = self._session
        if session is None:
            session = aiohttp.ClientSession()
            close_session = True

        try:
            # First attempt standard GET as documented in Developer Portal v2
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 405:
                    # Fallback to POST for v1 compatibility
                    async with session.post(
                        url,
                        json={"refresh_token": self._refresh_token},
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as post_resp:
                        return await self._parse_token_response(post_resp)
                return await self._parse_token_response(resp)
        except aiohttp.ClientError as err:
            raise AtombergConnectionError(f"Network error refreshing access token: {err}") from err
        finally:
            if close_session:
                await session.close()

    async def _parse_token_response(self, resp: aiohttp.ClientResponse) -> str:
        """Parse access token JSON response from developer endpoint."""
        data: dict[str, Any] = await resp.json(content_type=None)
        if resp.status != 200 or not data.get("status", True):
            error_msg = data.get("message") or data.get("error") or f"HTTP {resp.status}"
            raise AtombergAuthError(f"Failed to refresh developer token: {error_msg}")

        token = data.get("access_token") or data.get("token") or data.get("data", {}).get("access_token")
        if not token:
            raise AtombergAuthError("Response did not contain an access_token")

        expires_in = data.get("expires_in", 3600)
        self._access_token = str(token)
        self._expires_at = time.time() + float(expires_in)
        return self._access_token

    async def get_auth_headers(self) -> dict[str, str]:
        """Return HTTP authorization headers with a valid Bearer token and API key."""
        token = await self.get_access_token()
        headers: dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers
