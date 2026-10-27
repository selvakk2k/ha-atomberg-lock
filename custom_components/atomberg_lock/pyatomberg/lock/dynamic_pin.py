"""Public HTTP API client for Atomberg smart lock guest PIN generation."""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp

from pyatomberg.const import DEVELOPER_API_BASE
from pyatomberg.exceptions import AtombergConnectionError

_LOGGER = logging.getLogger(__name__)


class AtombergDynamicPinClient:
    """Generates temporary guest OTP keypad PINs via Atomberg's public developer API."""

    def __init__(
        self,
        api_key: str,
        access_token: str,
        base_url: str = DEVELOPER_API_BASE,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize dynamic PIN client."""
        self._api_key = api_key
        self._access_token = access_token
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._owns_session = session is None

    async def generate_guest_pin(
        self,
        device_id: str,
        series: str = "smart_lock_black",
        duration_hours: float = 4.0,
        start_time: int | None = None,
    ) -> dict[str, Any]:
        """Generate a temporary guest PIN for a lock keypad.

        For series 'smart_lock_black' (Azhero / SL1), a start_time and end_time window is required.
        For series 'G1' (Cypheo Elite), the PIN is clock-derived and valid for ~15 minutes.
        """
        url = f"{self._base_url}/get_lock_dynamic_pin"
        headers = {
            "x-api-key": self._api_key,
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

        body: dict[str, Any] = {"device_id": device_id}
        if series == "smart_lock_black":
            start = start_time or int(time.time())
            body["start_time"] = start
            body["end_time"] = start + int(duration_hours * 3600)

        session = self._session
        if session is None or session.closed:
            session = aiohttp.ClientSession()
            self._session = session

        try:
            async with session.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data: dict[str, Any] = await resp.json(content_type=None)
                if resp.status != 200 or not data.get("status", True):
                    error_msg = data.get("message") or f"HTTP {resp.status}"
                    raise AtombergConnectionError(f"Failed to generate lock PIN: {error_msg}")

                pin_data = data.get("message", {}).get("data") or data.get("data", {})
                return pin_data
        except aiohttp.ClientError as err:
            raise AtombergConnectionError(f"Network error requesting dynamic PIN: {err}") from err
        finally:
            if self._owns_session and self._session and not self._session.closed:
                await self._session.close()

    async def generate_remote_otp(
        self,
        device_id: str,
        series: str = "smart_lock_black",
        arrival_window_minutes: int = 15,
    ) -> dict[str, Any]:
        """Generate an immediate single-use Remote OTP for a lock keypad.

        This PIN works strictly once and is spent immediately upon unlocking.
        """
        now = int(time.time())
        end_time = now + (arrival_window_minutes * 60)
        res = await self.generate_guest_pin(
            device_id=device_id,
            series=series,
            start_time=now,
            duration_hours=max(0.25, arrival_window_minutes / 60.0),
        )
        pin_code = res.get("pin") or res.get("dynamic_pin") or res.get("code") or str(res)
        return {
            "otp": pin_code,
            "type": "remote_one_time",
            "device_id": device_id,
            "created_at": now,
            "expires_at": end_time,
            "raw": res,
        }

    async def generate_timed_otp(
        self,
        device_id: str,
        duration_hours: float = 4.0,
        start_time: int | None = None,
        series: str = "smart_lock_black",
    ) -> dict[str, Any]:
        """Generate a single-use Timed OTP that expires after the specified duration.

        If not punched into the keypad before expiration, the lock hardware rejects it.
        """
        now = start_time or int(time.time())
        end_time = now + int(duration_hours * 3600)
        res = await self.generate_guest_pin(
            device_id=device_id,
            series=series,
            start_time=now,
            duration_hours=duration_hours,
        )
        pin_code = res.get("pin") or res.get("dynamic_pin") or res.get("code") or str(res)
        return {
            "otp": pin_code,
            "type": "timed",
            "device_id": device_id,
            "created_at": now,
            "expires_at": end_time,
            "duration_hours": duration_hours,
            "raw": res,
        }
