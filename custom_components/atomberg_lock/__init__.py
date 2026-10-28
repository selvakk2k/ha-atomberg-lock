"""Atomberg Smart Lock custom integration for Home Assistant.

Based on protocol reverse engineering by dreadedlama (https://github.com/dreadedlama/atomberg-lock)
under GNU General Public License v3.0.
"""

from __future__ import annotations

import logging
from pathlib import Path
import sys

# Ensure bundled pyatomberg can be imported directly
_LIB_PATH = str(Path(__file__).parent)
if _LIB_PATH not in sys.path:
    sys.path.insert(0, _LIB_PATH)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import AtombergLockCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.LOCK, Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Atomberg Smart Lock from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = AtombergLockCoordinator(hass, entry)
    await coordinator.async_start()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Initial battery & status check in background
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), f"{DOMAIN}_initial_refresh_{entry.entry_id}"
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    async def async_handle_generate_otp(call: ServiceCall) -> ServiceResponse:
        """Handle generate OTP service call."""
        coordinator: AtombergLockCoordinator | None = None
        device_id = call.data.get("device_id")
        if device_id:
            dev_reg = dr.async_get(hass)
            device = dev_reg.async_get(device_id)
            if device:
                for c_entry_id in device.config_entries:
                    if c_entry_id in hass.data.get(DOMAIN, {}):
                        coordinator = hass.data[DOMAIN][c_entry_id]
                        break
        if coordinator is None and hass.data.get(DOMAIN):
            coordinator = next(iter(hass.data[DOMAIN].values()))

        if coordinator is None:
            raise HomeAssistantError("No Atomberg lock available to generate OTP.")

        duration_hours = float(call.data.get("duration_hours", 0.5))
        return await coordinator.async_generate_otp(duration_hours=duration_hours)

    if not hass.services.has_service(DOMAIN, "generate_otp"):
        hass.services.async_register(
            DOMAIN,
            "generate_otp",
            async_handle_generate_otp,
            supports_response=SupportsResponse.OPTIONAL,
        )

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options when changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: AtombergLockCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator.async_stop()
        if coordinator._relock_task and not coordinator._relock_task.done():
            coordinator._relock_task.cancel()
        if coordinator._lock_device:
            await coordinator._lock_device.disconnect()

    return unload_ok
