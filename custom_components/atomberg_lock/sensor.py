"""Sensor platform for Atomberg Smart Lock integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyatomberg.lock.hsj_protocol import slot_name

from .const import DOMAIN
from .coordinator import AtombergLockCoordinator
from .entity import AtombergLockBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Atomberg lock sensor entities from a config entry."""
    coordinator: AtombergLockCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([
        AtombergBatterySensor(coordinator),
        AtombergLastUnlockMethodSensor(coordinator),
        AtombergLastUserSensor(coordinator),
        AtombergLastEventSensor(coordinator),
        AtombergLastGeneratedOtpSensor(coordinator),
    ])


class AtombergBatterySensor(AtombergLockBaseEntity, SensorEntity):
    """Battery level sensor for Atomberg smart lock."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AtombergLockCoordinator) -> None:
        """Initialize the battery sensor."""
        super().__init__(coordinator, key="battery")
        self._attr_name = "Battery"

    @property
    def native_value(self) -> int | None:
        """Return the current battery level."""
        return self.coordinator.battery_level


class AtombergLastUnlockMethodSensor(AtombergLockBaseEntity, SensorEntity):
    """Sensor reporting the credential type of the last unlock event."""

    _attr_icon = "mdi:key"

    def __init__(self, coordinator: AtombergLockCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, key="last_unlock_method")
        self._attr_name = "Last Unlock Method"

    @property
    def native_value(self) -> str | None:
        """Return the last credential type used."""
        if not self.coordinator.last_event:
            return None
        return self.coordinator.last_event.credential_type


class AtombergLastUserSensor(AtombergLockBaseEntity, SensorEntity):
    """Sensor reporting the user or slot of the last unlock attempt."""

    _attr_icon = "mdi:account"

    def __init__(self, coordinator: AtombergLockCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, key="last_user")
        self._attr_name = "Last User"

    @property
    def native_value(self) -> str | None:
        """Return the resolved user or slot name."""
        event = self.coordinator.last_event
        if not event:
            return None
        if event.slot_id in (0, 0xFFFF, 65535) or not event.slot_id:
            return event.credential_type or event.event
        if self.coordinator.slot_mappings:
            if event.slot_id in self.coordinator.slot_mappings:
                return self.coordinator.slot_mappings[event.slot_id]
            if str(event.slot_id) in self.coordinator.slot_mappings:
                return self.coordinator.slot_mappings[str(event.slot_id)]
        if event.credential_type and event.credential_type != "Physical Credential":
            return f"{event.credential_type} {event.slot_id}"
        return slot_name(event.slot_id, self.coordinator.slot_mappings)


class AtombergLastEventSensor(AtombergLockBaseEntity, SensorEntity):
    """Sensor reporting the last recorded hardware event."""

    _attr_icon = "mdi:history"

    def __init__(self, coordinator: AtombergLockCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, key="last_event")
        self._attr_name = "Last Event"

    @property
    def native_value(self) -> str | None:
        """Return the description of the last event."""
        if not self.coordinator.last_event:
            return None
        return self.coordinator.last_event.event


class AtombergLastGeneratedOtpSensor(AtombergLockBaseEntity, SensorEntity):
    """Sensor reporting the last generated offline OTP PIN."""

    _attr_icon = "mdi:shield-key"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AtombergLockCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, key="last_generated_otp")
        self._attr_name = "Last Generated OTP"

    @property
    def native_value(self) -> str | None:
        """Return the last generated OTP PIN."""
        return self.coordinator.last_generated_otp

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {}
        if self.coordinator.last_otp_expires_at:
            attrs["expires_at"] = self.coordinator.last_otp_expires_at
        if self.coordinator.last_otp_valid_until_ts:
            attrs["valid_until_timestamp"] = self.coordinator.last_otp_valid_until_ts
        if self.coordinator.last_otp_duration_hours is not None:
            attrs["duration_hours"] = self.coordinator.last_otp_duration_hours
        return attrs
