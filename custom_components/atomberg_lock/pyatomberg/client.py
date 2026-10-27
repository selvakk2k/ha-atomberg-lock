"""Unified Atomberg client for device discovery, commands, and telemetry."""

import logging
from typing import Any

import aiohttp

from pyatomberg.auth.cognito import CognitoOAuthHandler
from pyatomberg.auth.developer import DeveloperTokenHandler
from pyatomberg.const import DEVELOPER_API_BASE
from pyatomberg.exceptions import AtombergAuthError, AtombergConnectionError
from pyatomberg.fan.device import AtombergFan
from pyatomberg.fan.udp_listener import AtombergUDPListener

_LOGGER = logging.getLogger(__name__)


class AtombergClient:
    """Unified client for Atomberg smart home devices."""

    def __init__(
        self,
        developer_token: str | None = None,
        api_key: str | None = None,
        cognito_handler: CognitoOAuthHandler | None = None,
        session: aiohttp.ClientSession | None = None,
        base_url: str = DEVELOPER_API_BASE,
        enable_udp_listener: bool = False,
    ) -> None:
        """Initialize AtombergClient."""
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._owns_session = session is None
        self._fans: dict[str, AtombergFan] = {}

        if developer_token:
            self._auth_handler: DeveloperTokenHandler | CognitoOAuthHandler = (
                DeveloperTokenHandler(
                    refresh_token=developer_token,
                    api_key=api_key,
                    session=self._session,
                    base_url=self._base_url,
                )
            )
        elif cognito_handler:
            self._auth_handler = cognito_handler
        else:
            raise AtombergAuthError("Either developer_token or cognito_handler must be provided")

        self._udp_listener: AtombergUDPListener | None = None
        if enable_udp_listener:
            self._udp_listener = AtombergUDPListener(callback=self._handle_udp_state)

    @property
    def auth_handler(self) -> DeveloperTokenHandler | CognitoOAuthHandler:
        """Return the active authentication handler."""
        return self._auth_handler

    @property
    def fans(self) -> dict[str, AtombergFan]:
        """Return dictionary of known fans keyed by device_id."""
        return self._fans

    async def __aenter__(self) -> "AtombergClient":
        """Async context manager entry."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
            if isinstance(self._auth_handler, DeveloperTokenHandler):
                self._auth_handler._session = self._session
        if self._udp_listener:
            await self._udp_listener.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close client sessions and background listeners."""
        if self._udp_listener and self._udp_listener.is_running:
            await self._udp_listener.stop()
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    def _handle_udp_state(self, device_id: str, payload: dict[str, Any]) -> None:
        """Handle real-time UDP broadcast packets received from local network."""
        fan = self._fans.get(device_id)
        if fan:
            fan.update_from_dict(payload)
            _LOGGER.debug("Updated fan %s from local UDP broadcast", device_id)
        else:
            _LOGGER.debug("Received broadcast for unregistered fan: %s", device_id)

    async def start_udp_listener(self) -> None:
        """Start local UDP broadcast listener on port 5625."""
        if not self._udp_listener:
            self._udp_listener = AtombergUDPListener(callback=self._handle_udp_state)
        await self._udp_listener.start()

    async def stop_udp_listener(self) -> None:
        """Stop local UDP broadcast listener."""
        if self._udp_listener:
            await self._udp_listener.stop()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Ensure active aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            if isinstance(self._auth_handler, DeveloperTokenHandler):
                self._auth_handler._session = self._session
        return self._session

    async def get_fans(self, update_states: bool = True) -> list[AtombergFan]:
        """Discover and retrieve registered ceiling fans."""
        session = await self._get_session()
        headers = await self._auth_handler.get_auth_headers()
        url = f"{self._base_url}/get_list_of_devices"

        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data: dict[str, Any] = await resp.json(content_type=None)
                if resp.status != 200 or not data.get("status", True):
                    error_msg = data.get("message") or f"HTTP {resp.status}"
                    raise AtombergConnectionError(f"Failed to fetch devices: {error_msg}")

                devices_raw = (
                    data.get("devices")
                    or data.get("data", {}).get("devices")
                    or data.get("data")
                    or []
                )

                discovered_fans: list[AtombergFan] = []
                for item in devices_raw:
                    if not isinstance(item, dict):
                        continue
                    device_id = item.get("device_id") or item.get("id")
                    if not device_id:
                        continue
                    name = item.get("device_name") or item.get("name") or str(device_id)
                    model_name = item.get("model") or item.get("series")

                    if device_id in self._fans:
                        fan = self._fans[device_id]
                        fan.name = name
                        fan.model_name = model_name
                    else:
                        fan = AtombergFan(
                            device_id=str(device_id),
                            name=str(name),
                            client=self,
                            model_name=model_name,
                            raw_data=item,
                        )
                        self._fans[device_id] = fan
                    discovered_fans.append(fan)

                if update_states and discovered_fans:
                    try:
                        await self.get_device_states("all")
                    except Exception as err:
                        _LOGGER.warning("Could not refresh initial device states: %s", err)

                return discovered_fans
        except aiohttp.ClientError as err:
            raise AtombergConnectionError(f"Network error querying device list: {err}") from err

    async def get_device_states(self, device_id: str = "all") -> dict[str, Any]:
        """Query state for a specific device or all devices."""
        session = await self._get_session()
        headers = await self._auth_handler.get_auth_headers()
        url = f"{self._base_url}/get_device_state"
        params = {"device_id": device_id}

        try:
            async with session.get(
                url,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data: dict[str, Any] = await resp.json(content_type=None)
                if resp.status != 200 or not data.get("status", True):
                    error_msg = data.get("message") or f"HTTP {resp.status}"
                    raise AtombergConnectionError(f"Failed to get device state: {error_msg}")

                state_data = data.get("data") or data.get("state") or data
                if isinstance(state_data, dict):
                    # Check if multi-device dictionary or single device
                    if device_id != "all" and device_id in self._fans:
                        self._fans[device_id].update_from_dict(state_data)
                    else:
                        for d_id, d_state in state_data.items():
                            if isinstance(d_state, dict) and d_id in self._fans:
                                self._fans[d_id].update_from_dict(d_state)
                elif isinstance(state_data, list):
                    for d_state in state_data:
                        if isinstance(d_state, dict):
                            d_id = d_state.get("device_id")
                            if d_id and d_id in self._fans:
                                self._fans[d_id].update_from_dict(d_state)

                return data
        except aiohttp.ClientError as err:
            raise AtombergConnectionError(f"Network error getting device state: {err}") from err

    async def send_command(self, device_id: str, command: dict[str, Any]) -> dict[str, Any]:
        """Send control command to a device via developer API."""
        session = await self._get_session()
        headers = await self._auth_handler.get_auth_headers()
        url = f"{self._base_url}/send_command"
        payload = {
            "device_id": device_id,
            "command": command,
        }

        try:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data: dict[str, Any] = await resp.json(content_type=None)
                if resp.status != 200 or not data.get("status", True):
                    error_msg = data.get("message") or f"HTTP {resp.status}"
                    raise AtombergConnectionError(f"Failed to send command: {error_msg}")
                return data
        except aiohttp.ClientError as err:
            raise AtombergConnectionError(f"Network error sending command: {err}") from err
