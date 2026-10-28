"""Diagnostics support for Atomberg Smart Lock."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_MAC, CONF_MASTER_KEY, CONF_SALT, DOMAIN

TO_REDACT = {
    CONF_MASTER_KEY,
    CONF_SALT,
    "master_key",
    "salt",
    "lock_salt",
    "dna_key",
    "aes",
    "adminAuthCode",
    "generalAuthCode",
    "tempAuthCode",
    "auth_code",
    "auth_token",
    "access_token",
    "refresh_token",
    "id_token",
    CONF_MAC,
    "mac",
    "mac_address",
    "address",
    "pin",
    "otp",
    "code",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry with sensitive data redacted."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    coordinator_data = {}
    if coordinator and hasattr(coordinator, "data") and coordinator.data:
        coordinator_data = async_redact_data(dict(coordinator.data), TO_REDACT)

    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "domain": entry.domain,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
            "pref_disable_new_entities": entry.pref_disable_new_entities,
            "pref_disable_polling": entry.pref_disable_polling,
        },
        "coordinator": {
            "last_update_success": getattr(coordinator, "last_update_success", None),
            "data": coordinator_data,
            "battery": getattr(coordinator, "battery", None),
            "last_event": getattr(coordinator, "last_event", None),
            "cred_type": getattr(coordinator, "cred_type", None),
            "slot_id": getattr(coordinator, "slot_id", None),
            "log_record_count": getattr(coordinator, "log_record_count", 0),
        },
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a device."""
    return await async_get_config_entry_diagnostics(hass, entry)
