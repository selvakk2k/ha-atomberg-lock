"""Atomberg smart door lock entity platform."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AtombergLockCoordinator
from .entity import AtombergLockBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Atomberg lock entity from a config entry."""
    coordinator: AtombergLockCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AtombergLockEntity(coordinator)])


class AtombergLockEntity(AtombergLockBaseEntity, LockEntity):
    """Represents an Atomberg smart door lock."""

    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator: AtombergLockCoordinator) -> None:
        """Initialize the lock entity."""
        super().__init__(coordinator, key="lock")
        self._attr_name = None  # Uses device name

    @property
    def is_locked(self) -> bool:
        """Return True if lock is currently locked."""
        return self.coordinator.is_locked

    @property
    def is_unlocking(self) -> bool:
        """Return True if an unlock operation is in progress."""
        return self.coordinator.is_unlocking

    @property
    def is_opening(self) -> bool:
        """Return True if an open operation is in progress."""
        return self.coordinator.is_unlocking

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return hardware event metadata."""
        event = self.coordinator.last_event
        if not event:
            return {}
        return {
            "last_event": event.event,
            "credential_type": event.credential_type,
            "slot_id": event.slot_id,
            "pin_code": event.pin_code,
            "last_event_time": event.timestamp,
        }

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the smart door lock."""
        await self.coordinator.async_unlock()

    async def async_open(self, **kwargs: Any) -> None:
        """Unlatch/open the smart door lock."""
        await self.coordinator.async_unlock()

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the smart door lock.

        Note: Atomberg locks use automatic latch engagement. If manually requested,
        we update the state to locked immediately.
        """
        self.coordinator.is_locked = True
        self.coordinator.async_set_updated_data(self.coordinator._build_data_dict())
