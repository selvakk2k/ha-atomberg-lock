"""Atomberg mobile cloud sync client for importing registered smart locks."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from pyatomberg.const import SERVER_API_BASE
from pyatomberg.exceptions import AtombergAuthError, AtombergConnectionError
from pyatomberg.lock.device import AtombergLock
from pyatomberg.lock.pairing import PairingResult

_LOGGER = logging.getLogger(__name__)


class AtombergCloudSync:
    """Fetches already-paired lock encryption keys and metadata from the Atomberg mobile backend."""

    def __init__(
        self,
        access_token: str | None = None,
        id_token: str | None = None,
        session: aiohttp.ClientSession | None = None,
        base_url: str = SERVER_API_BASE,
    ) -> None:
        """Initialize cloud sync client with Cognito access token or ID token."""
        token = access_token or id_token
        if not token:
            raise AtombergAuthError("Cognito access token or ID token is required for cloud sync")

        self._token = token
        self._session = session
        self._owns_session = session is None
        self._base_url = base_url.rstrip("/")

    @property
    def token(self) -> str:
        """Return the active Bearer token."""
        return self._token

    async def fetch_account_locks(self) -> list[PairingResult]:
        """Fetch all smart locks registered to this Atomberg account with encryption parameters."""
        url = f"{self._base_url}/app_open"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "Atomberg/7.7.2",
            "Accept": "application/json",
        }

        session = self._session
        if session is None or session.closed:
            session = aiohttp.ClientSession()
            self._session = session

        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    raise AtombergConnectionError(f"Cloud server returned HTTP {resp.status}")

                data: dict[str, Any] = await resp.json(content_type=None)

                # Extract devices list from all possible schemas
                devices: list[dict[str, Any]] = []

                # Primary production schema: message.user_details.families[].devices[]
                user_details = data.get("message", {}).get("user_details", {})
                families = user_details.get("families", [])
                for fam in families:
                    if isinstance(fam, dict):
                        for dev in fam.get("devices", []):
                            if isinstance(dev, dict):
                                devices.append(dev)

                # Fallback flat lists
                if not devices:
                    devices = (
                        data.get("devices")
                        or data.get("data", {}).get("devices")
                        or data.get("data", {}).get("locks")
                        or data.get("locks")
                        or []
                    )

                results: list[PairingResult] = []
                for item in devices:
                    if not isinstance(item, dict):
                        continue

                    meta = item.get("metadata", {})
                    friendly_name = item.get("friendly_name") or item.get("name") or "Atomberg Smart Lock"
                    model = item.get("model") or item.get("series") or "smart_lock"

                    # 1. Primary Atomberg lock metadata schema (aes + adminAuthCode/generalAuthCode)
                    if "aes" in meta and ("adminAuthCode" in meta or "generalAuthCode" in meta):
                        mac_raw = meta.get("lockMac") or item.get("device_id") or ""
                        mac_clean = mac_raw.replace(":", "").replace("-", "").upper()
                        if len(mac_clean) == 12:
                            mac = ":".join(mac_clean[i : i + 2] for i in range(0, 12, 2))
                        else:
                            mac = str(mac_raw).upper()

                        key_str = str(meta["aes"]).strip()
                        key_bytes = key_str.encode("utf-8") if len(key_str) == 16 else bytes.fromhex(key_str)

                        salt_hex = str(meta.get("adminAuthCode") or meta.get("generalAuthCode", "")).strip()
                        salt_bytes = bytes.fromhex(salt_hex)

                        temp_code = meta.get("tempAuthCode")
                        gen_code = meta.get("generalAuthCode")

                        results.append(
                            PairingResult(
                                mac_address=mac,
                                master_key=key_bytes,
                                salt=salt_bytes,
                                name=friendly_name,
                                model=model,
                                temp_auth_code=str(temp_code).strip() if temp_code else None,
                                general_auth_code=str(gen_code).strip() if gen_code else None,
                            )
                        )
                        continue

                    # 2. Legacy / generic fallback schema
                    dev_type = item.get("device_type", item.get("type", "")).lower()
                    desc = str(meta.get("description", "")).lower()
                    if "lock" not in dev_type and "sitana" not in dev_type and "lock" not in desc and "lock" not in model.lower():
                        continue

                    mac_val = item.get("mac_address") or item.get("mac") or item.get("device_id")
                    raw_key = item.get("master_key") or item.get("static_key") or item.get("key") or meta.get("key")
                    raw_salt = item.get("salt") or item.get("lock_salt") or meta.get("salt")

                    if not mac_val or not raw_key or not raw_salt:
                        continue

                    if isinstance(raw_key, str):
                        raw_key_str = raw_key.strip()
                        key_bytes = bytes.fromhex(raw_key_str) if len(raw_key_str) == 32 else raw_key_str.encode("utf-8")
                    else:
                        key_bytes = bytes(raw_key)

                    if isinstance(raw_salt, str):
                        salt_clean = raw_salt.strip().replace(" ", "").replace("0x", "")
                        salt_bytes = bytes.fromhex(salt_clean)
                    else:
                        salt_bytes = bytes(raw_salt)

                    results.append(
                        PairingResult(
                            mac_address=str(mac_val).upper(),
                            master_key=key_bytes,
                            salt=salt_bytes,
                            name=friendly_name,
                            model=model,
                        )
                    )

                return results
        except aiohttp.ClientError as err:
            raise AtombergConnectionError(f"Network error querying app_open: {err}") from err
        finally:
            if self._owns_session and self._session and not self._session.closed:
                await self._session.close()
