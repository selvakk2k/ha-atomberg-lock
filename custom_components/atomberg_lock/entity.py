"""Shared base entity for Atomberg Smart Lock integration.

Enforces cross-platform parity across lock and sensor siblings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN, MANUFACTURER

if TYPE_CHECKING:
    from .coordinator import AtombergLockCoordinator


class AtombergLockBaseEntity(CoordinatorEntity["AtombergLockCoordinator"]):
    """Shared base entity for Atomberg lock platforms."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: "AtombergLockCoordinator", key: str) -> None:
        """Initialize the base entity."""
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.address.lower()}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address.upper())},
            connections={(CONNECTION_BLUETOOTH, coordinator.address.upper())},
            name=DEFAULT_NAME,
            manufacturer=MANUFACTURER,
            model="SL1 Pro",
        )

    @property
    def available(self) -> bool:
        """Return True if coordinator is initialized and device is available."""
        return self.coordinator.is_available
