"""DataUpdateCoordinator for Atomberg Smart Lock integration.

Based on protocol reverse engineering by dreadedlama (https://github.com/dreadedlama/atomberg-lock)
under GNU General Public License v3.0.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .ble import AtombergLockBLE
from pyatomberg.lock.hsj_protocol import LiveEvent, format_ist_time

from .const import AUTO_LOCK_SECONDS, CONF_MAC, CONF_MASTER_KEY, CONF_SALT, CONF_SLOT_MAPPINGS, DOMAIN

_LOGGER = logging.getLogger(__name__)


class AtombergLockCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinates BLE communication and state management for an Atomberg smart lock."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        scan_interval = entry.options.get("scan_interval_minutes", 30)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.data[CONF_MAC]}",
            update_interval=timedelta(minutes=scan_interval),  # Periodic battery & log sync
        )
        self.entry = entry
        self.address = str(entry.data[CONF_MAC]).upper()
        self.model: str = str(entry.data.get("model") or entry.options.get("model") or "SL1 Pro")

        raw_key = entry.data[CONF_MASTER_KEY]
        if isinstance(raw_key, str):
            raw_key_str = raw_key.strip()
            if len(raw_key_str) == 32:
                try:
                    self.master_key = bytes.fromhex(raw_key_str)
                except ValueError:
                    self.master_key = raw_key_str.encode("utf-8")
            else:
                self.master_key = raw_key_str.encode("utf-8")
        else:
            self.master_key = bytes(raw_key)

        raw_salt = entry.data[CONF_SALT]
        if isinstance(raw_salt, str):
            self.lock_salt = bytes.fromhex(raw_salt.strip().replace(" ", "").replace("0x", ""))
        else:
            self.lock_salt = bytes(raw_salt)

        raw_mappings = entry.options.get(CONF_SLOT_MAPPINGS, {})
        self.slot_mappings: dict[int, str] = {
            int(k): str(v) for k, v in raw_mappings.items() if str(k).isdigit()
        }
        self.general_auth_code: str = str(entry.data.get("general_auth_code") or entry.options.get("general_auth_code") or "")
        self.temp_auth_code: str = str(entry.data.get("temp_auth_code") or entry.options.get("temp_auth_code") or "")

        # Runtime states - persistent availability for battery BLE devices
        self.is_locked = True
        self.is_unlocking = False
        self.battery_level: int | None = entry.data.get("battery_level")
        self.last_event: LiveEvent | None = None
        self.is_available = True
        self.discovered_slots: dict[int, str] = {14: "Fingerprint"}

        # Sync reporting diagnostics
        self.last_sync_timestamp: str | None = None
        self.last_sync_ble_status: str = "Idle"
        self.last_sync_ble_count: int = 0
        self.last_sync_cloud_status: str = "Idle"
        self.last_sync_cloud_count: int = 0

        self._lock_device: AtombergLockBLE | None = None
        self._busy = asyncio.Lock()
        self._relock_task: asyncio.Task[None] | None = None
        self._cancel_bluetooth: Any = None
        self._log_store = Store[list[dict[str, Any]]](hass, 1, f"{DOMAIN}_{entry.entry_id}_logs")
        self.unlock_history: list[dict[str, Any]] = []
        self._loaded_options: dict[str, Any] = dict(entry.options)

    async def async_start(self) -> None:
        """Start listening for live Bluetooth advertisements via ESPHome Bluetooth Proxies."""
        try:
            stored_logs = await self._log_store.async_load()
            if stored_logs and isinstance(stored_logs, list):
                self.unlock_history = stored_logs
                if not self.last_event and self.unlock_history:
                    latest = self.unlock_history[0]
                    now_ts = int(time.time())
                    self.last_event = LiveEvent(
                        battery=latest.get("battery", self.battery_level or 100),
                        event=latest.get("event", "Hardware Event"),
                        credential_type=latest.get("credential_type", "Physical Credential"),
                        slot_id=latest.get("slot_id") or latest.get("slot", 0),
                        pin_code=latest.get("pin", ""),
                        timestamp=latest.get("timestamp") or latest.get("datetime", format_ist_time(now_ts)),
                        raw_timestamp=latest.get("raw_timestamp", now_ts),
                        is_unlocked="Unlocked" in latest.get("event", ""),
                        is_auto_lock="Auto-Lock" in latest.get("event", ""),
                        is_tamper=False,
                    )
                self.async_set_updated_data(self._build_data_dict())
        except Exception as err:
            _LOGGER.debug("Could not load stored unlock history: %s", err)

        self._cancel_bluetooth = bluetooth.async_register_callback(
            self.hass,
            self._handle_bluetooth_event,
            bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )

    def async_stop(self) -> None:
        """Stop Bluetooth advertisement listeners."""
        if self._cancel_bluetooth:
            self._cancel_bluetooth()
            self._cancel_bluetooth = None

    @callback
    def _handle_bluetooth_event(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle incoming Bluetooth advertisement from lock."""
        _LOGGER.debug(
            "Atomberg lock %s advertisement received: RSSI %s",
            self.address,
            service_info.rssi,
        )
        self.is_available = True
        if self._lock_device:
            self._lock_device.ble_device = service_info.device
        self.async_set_updated_data(self._build_data_dict())

    def _get_ble_device(self) -> Any:
        """Retrieve current BLEDevice from Home Assistant Bluetooth stack."""
        dev = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if dev is None:
            dev = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=False
            )
        return dev

    def _ensure_lock_instance(self) -> AtombergLockBLE:
        """Instantiate or update AtombergLockBLE with latest BLE device reference."""
        ble_dev = self._get_ble_device()
        if self._lock_device is None:
            self._lock_device = AtombergLockBLE(
                mac_address=self.address,
                master_key=self.master_key,
                salt=self.lock_salt,
                ble_device=ble_dev,
                slot_mappings=self.slot_mappings,
            )
            self._lock_device.register_event_callback(self._handle_live_event)
        else:
            self._lock_device.ble_device = ble_dev
            self._lock_device.slot_mappings = self.slot_mappings

        return self._lock_device

    def _handle_live_event(self, event: LiveEvent) -> None:
        """Handle real-time notification from lock hardware."""
        _LOGGER.debug("Received live lock event: %s (%s)", event.event, event.credential_type)
        self.last_event = event
        if event.slot_id and event.slot_id not in (0, 0xFFFF, 65535):
            self.discovered_slots[event.slot_id] = event.credential_type or "Slot"

        if event.is_unlocked:
            self.is_locked = False
            self._schedule_auto_relock()

        if event.is_auto_lock:
            self.is_locked = True

        if event.battery is not None and 0 <= event.battery <= 100:
            self.battery_level = event.battery

        self.async_set_updated_data(self._build_data_dict())

    def _schedule_auto_relock(self) -> None:
        """Schedule optimistic relock matching hardware latch timeout."""
        if self._relock_task and not self._relock_task.done():
            self._relock_task.cancel()

        auto_lock = self.entry.options.get("auto_lock_seconds", AUTO_LOCK_SECONDS)

        async def _relock_coro() -> None:
            await asyncio.sleep(auto_lock)
            self.is_locked = True
            self.is_unlocking = False
            self.async_set_updated_data(self._build_data_dict())

        self._relock_task = self.hass.async_create_background_task(
            _relock_coro(), f"{DOMAIN}_auto_relock_{self.address}"
        )

    def _merge_unlock_records(self, new_records: list[dict[str, Any]]) -> None:
        """Merge unlock records, deduplicating by raw_timestamp into a new list instance."""
        existing_ts = {r.get("raw_timestamp") for r in self.unlock_history if r.get("raw_timestamp")}
        merged = list(self.unlock_history)
        for rec in new_records:
            ts = rec.get("raw_timestamp")
            if ts and ts not in existing_ts:
                merged.append(rec)
                existing_ts.add(ts)
            elif not ts:
                merged.append(rec)
        merged.sort(key=lambda x: x.get("raw_timestamp", 0), reverse=True)
        if len(merged) > 100:
            merged = merged[:100]
        self.unlock_history = merged

    @property
    def is_cloud_connected(self) -> bool:
        """Return True if lock has active Atomberg cloud credentials."""
        return bool(
            self.entry.options.get("auth_token")
            or self.entry.data.get("auth_token")
            or self.entry.options.get("refresh_token")
            or self.entry.data.get("refresh_token")
        )

    def _get_cloud_sync_client(self) -> Any | None:
        """Create and return an AtombergCloudSync client with auto-refresh support."""
        auth_token = self.entry.options.get("auth_token") or self.entry.data.get("auth_token")
        refresh_token = self.entry.options.get("refresh_token") or self.entry.data.get("refresh_token")

        if not auth_token and not refresh_token:
            return None

        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        from pyatomberg.lock.cloud_sync import AtombergCloudSync

        session = async_get_clientsession(self.hass)

        async def _on_token_refreshed(new_token: str, new_refresh: str | None) -> None:
            """Persist refreshed tokens to config entry options and data."""
            new_data = dict(self.entry.data)
            new_data["auth_token"] = new_token
            if new_refresh:
                new_data["refresh_token"] = new_refresh

            new_options = dict(self.entry.options)
            if "auth_token" in new_options:
                new_options["auth_token"] = new_token
            if new_refresh and "refresh_token" in new_options:
                new_options["refresh_token"] = new_refresh

            self.hass.config_entries.async_update_entry(
                self.entry,
                data=new_data,
                options=new_options,
            )
            # Synchronize internal snapshot so listener does not treat token refresh as an options change
            self._loaded_options = dict(new_options)
            _LOGGER.info("Persisted refreshed Cognito tokens to config entry %s", self.entry.entry_id)

        return AtombergCloudSync(
            access_token=auth_token,
            refresh_token=refresh_token,
            session=session,
            on_token_refreshed=_on_token_refreshed,
        )

    @property
    def is_wifi_lock(self) -> bool:
        """Return True if the lock model has Wi-Fi connectivity (e.g. Cyphero / G1 series)."""
        model_val = getattr(self, "model", None) or self.entry.data.get("model") or "SL1 Pro"
        return any(m in str(model_val).lower() for m in ("cyphero", "wifi", "g1"))

    def _build_data_dict(self) -> dict[str, Any]:
        """Build summary data dictionary for listeners."""
        return {
            "is_locked": self.is_locked,
            "is_unlocking": self.is_unlocking,
            "is_cloud_connected": self.is_cloud_connected,
            "is_wifi_lock": self.is_wifi_lock,
            "battery_level": self.battery_level,
            "last_event": self.last_event.event if self.last_event else None,
            "credential_type": self.last_event.credential_type if self.last_event else None,
            "slot_id": self.last_event.slot_id if self.last_event else None,
            "pin_code": self.last_event.pin_code if self.last_event else None,
            "timestamp": self.last_event.timestamp if self.last_event else None,
            "last_sync_timestamp": self.last_sync_timestamp,
            "last_sync_ble_status": self.last_sync_ble_status,
            "last_sync_ble_count": self.last_sync_ble_count,
            "last_sync_cloud_status": self.last_sync_cloud_status,
            "last_sync_cloud_count": self.last_sync_cloud_count,
            "unlock_history": self.unlock_history,
            "unlock_count": len(self.unlock_history),
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll lock status and battery periodically over BLE."""
        self.is_available = True
        ble_dev = self._get_ble_device()
        if not ble_dev:
            # Device is currently asleep, maintain available state
            return self._build_data_dict()

        lock = self._ensure_lock_instance()

        # Connect briefly to query battery
        async with self._busy:
            try:
                await lock.connect(timeout=10.0)
                await lock.authenticate()
                battery = await lock.get_battery()
                if battery is not None:
                    self.battery_level = battery
                    if self.last_event:
                        self.last_event.battery = battery
            except Exception as err:
                _LOGGER.debug("Could not poll lock battery: %s", err)
            finally:
                await lock.disconnect()

        return self._build_data_dict()

    async def async_unlock(self) -> None:
        """Dispatch unlock command over BLE."""
        lock = self._ensure_lock_instance()
        self.is_unlocking = True
        self.async_set_updated_data(self._build_data_dict())

        async with self._busy:
            try:
                await lock.connect(timeout=15.0)
                await lock.authenticate()
                await lock.unlock()
                self.is_locked = False
                self.is_unlocking = False

                # Immediately query battery status while BLE session is active
                try:
                    battery = await lock.get_battery()
                    if battery is not None:
                        self.battery_level = battery
                except Exception as b_err:
                    _LOGGER.debug("Could not poll battery after unlock: %s", b_err)

                # Record unlock event
                now_ts = int(time.time())
                self.last_event = LiveEvent(
                    battery=self.battery_level or 100,
                    event="Unlocked via Bluetooth",
                    credential_type="Home Assistant (BLE)",
                    slot_id=0,
                    pin_code="",
                    timestamp=format_ist_time(now_ts),
                    raw_timestamp=now_ts,
                    is_unlocked=True,
                    is_auto_lock=False,
                    is_tamper=False,
                )

                self._schedule_auto_relock()
                self.async_set_updated_data(self._build_data_dict())
            except Exception as err:
                self.is_unlocking = False
                self.async_set_updated_data(self._build_data_dict())
                raise HomeAssistantError(f"Failed to unlock Atomberg lock: {err}") from err
            finally:
                await lock.disconnect()

    async def async_sync_device(self) -> dict[str, Any]:
        """Synchronize lock battery and event logs via BLE and optionally Cloud."""
        lock = self._ensure_lock_instance()
        ble_success = False
        ble_error: str | None = None
        ble_new_count = 0
        cloud_new_count = 0
        cloud_status = "Not Configured"

        now_ts = int(time.time())
        self.last_sync_timestamp = format_ist_time(now_ts)

        # 1. Attempt Bluetooth Low Energy sync
        async with self._busy:
            try:
                await lock.connect(timeout=10.0)
                await lock.authenticate()
                ble_success = True

                # Query Battery
                try:
                    battery = await lock.get_battery()
                    if battery is not None:
                        self.battery_level = battery
                except Exception as b_err:
                    _LOGGER.debug("Could not poll battery during sync: %s", b_err)

                # Fetch Flash Event Logs over BLE
                try:
                    records = await lock.fetch_incremental_logs(start_offset=0)
                    if records:
                        before_count = len(self.unlock_history)
                        self._merge_unlock_records(records)
                        ble_new_count = len(self.unlock_history) - before_count
                        latest = self.unlock_history[0] if self.unlock_history else records[0]
                        self.last_event = LiveEvent(
                            battery=latest.get("battery", self.battery_level or 100),
                            event=latest.get("event", "Hardware Event"),
                            credential_type=latest.get("credential_type", "Physical Credential"),
                            slot_id=latest.get("slot_id") or latest.get("slot", 0),
                            pin_code=latest.get("pin", ""),
                            timestamp=latest.get("timestamp") or latest.get("datetime", format_ist_time(now_ts)),
                            raw_timestamp=latest.get("raw_timestamp", now_ts),
                            is_unlocked="Unlocked" in latest.get("event", ""),
                            is_auto_lock="Auto-Lock" in latest.get("event", ""),
                            is_tamper=False,
                        )
                except Exception as l_err:
                    _LOGGER.debug("Could not fetch logs via BLE during sync: %s", l_err)

            except Exception as b_err:
                ble_error = str(b_err)
                _LOGGER.warning("BLE sync currently unavailable for %s: %s", self.address, b_err)
            finally:
                try:
                    await lock.disconnect()
                except Exception as d_err:
                    _LOGGER.debug("Error disconnecting lock during sync cleanup: %s", d_err)

        self.last_sync_ble_status = "Connected" if ble_success else f"Unreachable ({ble_error or 'timeout'})"
        self.last_sync_ble_count = ble_new_count

        # 2. Query Cloud Firestore history ONLY if device is cloud-connected (Manual devices remain 100% offline)
        if self.is_cloud_connected:
            try:
                sync_client = self._get_cloud_sync_client()
                if sync_client:
                    cloud_records = await sync_client.fetch_cloud_history(
                        device_id=self.address,
                        slot_mappings=self.slot_mappings,
                    )
                if cloud_records:
                    before_count = len(self.unlock_history)
                    self._merge_unlock_records(cloud_records)
                    cloud_new_count = len(self.unlock_history) - before_count
                    cloud_status = f"Success ({len(cloud_records)} total in cloud)"
                    # If we don't have a live BLE event yet, populate from latest cloud record
                    if not ble_success and self.unlock_history and not self.last_event:
                        latest_cloud = self.unlock_history[0]
                        c_ts = latest_cloud.get("raw_timestamp", int(time.time()))
                        self.last_event = LiveEvent(
                            battery=self.battery_level or 100,
                            event=latest_cloud.get("event", "Hardware Event"),
                            credential_type=latest_cloud.get("credential_type", "Physical Credential"),
                            slot_id=latest_cloud.get("slot", 0),
                            pin_code="",
                            timestamp=latest_cloud.get("datetime", format_ist_time(c_ts)),
                            raw_timestamp=c_ts,
                            is_unlocked="Unlocked" in latest_cloud.get("event", ""),
                            is_auto_lock=False,
                            is_tamper=False,
                        )
                else:
                    cloud_status = "0 records in cloud"
            except Exception as c_err:
                cloud_status = f"Error: {c_err}"
                _LOGGER.debug("Could not fetch cloud logs: %s", c_err)
        else:
            cloud_status = "Offline (Manual Config)"

        self.last_sync_cloud_status = cloud_status
        self.last_sync_cloud_count = cloud_new_count

        # Save merged history
        try:
            await self._log_store.async_save(self.unlock_history)
        except Exception as s_err:
            _LOGGER.debug("Could not save unlock history: %s", s_err)

        self.async_set_updated_data(self._build_data_dict())

        # Notify user with clear diagnostic summary
        from homeassistant.components import persistent_notification
        clean_mac = self.address.replace(":", "_").lower()
        total_history = len(self.unlock_history)
        ble_msg = (
            f"**Bluetooth Low Energy:** Connected (fetched {ble_new_count} new records from lock flash)"
            if ble_success
            else f"**Bluetooth Low Energy:** Lock unreachable ({ble_error or 'Lock may be asleep or out of range'})"
        )
        cloud_msg = f"**Atomberg Cloud:** {cloud_status} (added {cloud_new_count} new records)"
        notification_msg = (
            f"### Atomberg Lock Sync Report\n\n"
            f"- {ble_msg}\n"
            f"- {cloud_msg}\n"
            f"- **Total Stored Activity:** {total_history} records\n"
            f"- **Timestamp:** {self.last_sync_timestamp}"
        )
        persistent_notification.async_create(
            self.hass,
            notification_msg,
            title="Atomberg Lock Sync Report",
            notification_id=f"atomberg_sync_{clean_mac}",
        )

        return {
            "ble_success": ble_success,
            "ble_new_count": ble_new_count,
            "cloud_status": cloud_status,
            "cloud_new_count": cloud_new_count,
            "total_history": total_history,
        }

