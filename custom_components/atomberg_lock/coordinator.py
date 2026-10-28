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

from pyatomberg.lock.device import AtombergLock
from pyatomberg.lock.hsj_protocol import LiveEvent, format_ist_time

from .const import AUTO_LOCK_SECONDS, CONF_MAC, CONF_MASTER_KEY, CONF_SALT, CONF_SLOT_MAPPINGS, DOMAIN

_LOGGER = logging.getLogger(__name__)


class AtombergLockCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinates BLE communication and state management for an Atomberg smart lock."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.data[CONF_MAC]}",
            update_interval=timedelta(minutes=30),  # Periodic battery & log sync
        )
        self.entry = entry
        self.address = str(entry.data[CONF_MAC]).upper()

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

        # Runtime states - persistent availability for battery BLE devices
        self.is_locked = True
        self.is_unlocking = False
        self.battery_level: int | None = entry.data.get("battery_level")
        self.last_event: LiveEvent | None = None
        self.is_available = True
        self.discovered_slots: dict[int, str] = {14: "Fingerprint"}

        self._lock_device: AtombergLock | None = None
        self._busy = asyncio.Lock()
        self._relock_task: asyncio.Task[None] | None = None
        self._cancel_bluetooth: Any = None
        self._log_store = Store[list[dict[str, Any]]](hass, 1, f"{DOMAIN}_{entry.entry_id}_logs")

    async def async_start(self) -> None:
        """Start listening for live Bluetooth advertisements via ESPHome Bluetooth Proxies."""
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

    def _ensure_lock_instance(self) -> AtombergLock:
        """Instantiate or update AtombergLock with latest BLE device reference."""
        ble_dev = self._get_ble_device()
        if self._lock_device is None:
            self._lock_device = AtombergLock(
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

        async def _relock_coro() -> None:
            await asyncio.sleep(AUTO_LOCK_SECONDS)
            self.is_locked = True
            self.is_unlocking = False
            self.async_set_updated_data(self._build_data_dict())

        self._relock_task = self.hass.async_create_background_task(
            _relock_coro(), f"{DOMAIN}_auto_relock_{self.address}"
        )

    def _build_data_dict(self) -> dict[str, Any]:
        """Build summary data dictionary for listeners."""
        return {
            "is_locked": self.is_locked,
            "is_unlocking": self.is_unlocking,
            "battery_level": self.battery_level,
            "last_event": self.last_event.event if self.last_event else None,
            "credential_type": self.last_event.credential_type if self.last_event else None,
            "slot_id": self.last_event.slot_id if self.last_event else None,
            "pin_code": self.last_event.pin_code if self.last_event else None,
            "timestamp": self.last_event.timestamp if self.last_event else None,
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

    async def async_sync_device(self) -> None:
        """Manually trigger full synchronization of lock battery and event logs."""
        lock = self._ensure_lock_instance()

        async with self._busy:
            try:
                await lock.connect(timeout=15.0)
                await lock.authenticate()

                # 1. Query Battery
                try:
                    battery = await lock.get_battery()
                    if battery is not None:
                        self.battery_level = battery
                except Exception as b_err:
                    _LOGGER.debug("Could not poll battery during sync: %s", b_err)

                # 2. Fetch Flash Event Logs
                try:
                    records = await lock.fetch_incremental_logs(start_offset=0)
                    if records:
                        # Find the most recent unlock event in the logs
                        latest_unlock = None
                        for rec in reversed(records):
                            evt = rec.get("event", "")
                            cred = rec.get("credential_type", "")
                            slot = rec.get("slot", 0)
                            if "Unlocked" in evt or cred in ("Fingerprint", "NFC Card", "PIN Code", "Atomberg App"):
                                if slot not in (0xFFFF, 65535):
                                    latest_unlock = rec
                                    break

                        latest = records[-1]
                        now_ts = int(time.time())

                        # For user and credential method, use the actual unlock record
                        target_rec = latest_unlock if latest_unlock else latest
                        slot_id = target_rec.get("slot", 0)
                        if slot_id in (0xFFFF, 65535):
                            slot_id = 0

                        self.last_event = LiveEvent(
                            battery=latest.get("battery", self.battery_level or 100),
                            event=latest.get("event", "Hardware Event"),
                            credential_type=target_rec.get("credential_type", "Physical Credential"),
                            slot_id=slot_id,
                            pin_code=target_rec.get("pin", ""),
                            timestamp=target_rec.get("datetime", format_ist_time(now_ts)),
                            raw_timestamp=now_ts,
                            is_unlocked="Unlocked" in latest.get("event", ""),
                            is_auto_lock="Auto-Lock" in latest.get("event", ""),
                            is_tamper=False,
                        )
                except Exception as l_err:
                    _LOGGER.debug("Could not fetch logs during sync: %s", l_err)

                self.async_set_updated_data(self._build_data_dict())
            except Exception as err:
                raise HomeAssistantError(f"Failed to sync Atomberg lock: {err}") from err
            finally:
                await lock.disconnect()

    async def async_generate_otp(self, duration_hours: float = 0.5) -> dict[str, Any]:
        """Generate an offline guest OTP for the lock keypad."""
        from pyatomberg.lock.dynamic_pin import calculate_offline_otp, quantize_expiration_time

        auth_code = str(
            self.entry.data.get("general_auth_code")
            or self.entry.data.get(CONF_MASTER_KEY)
            or ""
        )
        aes_key = str(
            self.entry.data.get("dna_key")
            or self.entry.data.get(CONF_MASTER_KEY)
            or ""
        )

        expires_dt = quantize_expiration_time(duration_hours=duration_hours)
        otp = calculate_offline_otp(
            auth_code=auth_code,
            aes_key=aes_key,
            target_dt=expires_dt,
            lock_mac=self.address,
        )

        _LOGGER.info(
            "Generated offline OTP for Atomberg lock %s: %s (expires: %s)",
            self.address,
            otp,
            expires_dt.isoformat(),
        )

        return {
            "otp": otp,
            "expires_at": expires_dt.isoformat(),
            "valid_until_timestamp": int(expires_dt.timestamp()),
            "duration_hours": duration_hours,
            "lock_mac": self.address,
        }

