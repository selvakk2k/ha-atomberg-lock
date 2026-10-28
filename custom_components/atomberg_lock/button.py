"""Button platform for Atomberg Smart Lock integration."""

from __future__ import annotations

from homeassistant.components import persistent_notification
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
    async_add_entities([
        AtombergSyncButton(coordinator),
        AtombergGenerateOtpButton(coordinator),
    ])


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


class AtombergGenerateOtpButton(AtombergLockBaseEntity, ButtonEntity):
    """Button to generate an offline guest OTP on demand."""

    _attr_icon = "mdi:shield-key-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: AtombergLockCoordinator) -> None:
        """Initialize the generate OTP button."""
        super().__init__(coordinator, key="generate_otp")
        self._attr_name = "Generate OTP"

    async def async_press(self) -> None:
        """Generate guest OTP and notify the user."""
        res = await self.coordinator.async_generate_otp(duration_hours=0.5)
        otp = res["otp"]
        expires_at = res["expires_at"]
        clean_mac = self.coordinator.address.replace(":", "_").lower()
        persistent_notification.async_create(
            self.hass,
            f"**Atomberg Guest OTP:** `{otp}`\n\n"
            f"**Valid Until:** {expires_at}\n\n"
            f"Enter this 6-digit code followed by `#` on the lock keypad.",
            title="Atomberg Smart Lock OTP",
            notification_id=f"atomberg_otp_{clean_mac}",
        )
