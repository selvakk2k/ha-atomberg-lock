"""Button platform for Atomberg Smart Lock integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AtombergLockCoordinator
from .entity import AtombergLockBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Atomberg lock button entities from a config entry."""
    coordinator: AtombergLockCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AtombergSyncButton(coordinator)])


class AtombergSyncButton(AtombergLockBaseEntity, ButtonEntity):
    """Button to trigger an on-demand status & log sync over BLE."""

    _attr_icon = "mdi:sync"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: AtombergLockCoordinator) -> None:
        """Initialize the sync button."""
        super().__init__(coordinator, key="sync_status")
        self._attr_name = "Sync Status & Logs"

    async def async_press(self) -> None:
        """Trigger lock status and event log synchronization."""
        await self.coordinator.async_sync_device()
