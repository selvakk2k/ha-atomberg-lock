"""Device data models for pyatomberg."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DeviceType(str, Enum):
    """Supported Atomberg device types."""
    FAN = "fan"
    LOCK = "lock"
    WATER_PURIFIER = "purifier"
    COOKTOP = "cooktop"
    UNKNOWN = "unknown"


@dataclass
class AtombergDeviceModel:
    """Base model for an Atomberg device."""
    device_id: str
    name: str
    device_type: DeviceType
    is_online: bool = True
    model_name: str | None = None
    firmware_version: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class FanState:
    """Live state for an Atomberg smart ceiling fan."""
    power: bool = False
    speed: int = 0
    sleep: bool = False
    timer_hours: int = 0
    led_brightness: int | None = None
    light_mode: str | None = None
    last_seen: float | None = None


@dataclass
class LockState:
    """Live state for an Atomberg smart door lock."""
    is_locked: bool = True
    battery_level: int = 100
    last_unlock_method: str | None = None
    last_unlock_user_id: int | None = None
    last_unlock_timestamp: float | None = None
    tamper_alert: bool = False
    auto_lock_engaged: bool = False


@dataclass
class WaterPurifierState:
    """Live state for an Atomberg water purifier."""
    input_tds: int = 0
    output_tds: int = 0
    filter_life_percent: int = 100
    tank_status: str = "tank_full"
    purification_mode: str = "ro_mode"
    water_consumed_liters: float = 0.0
    water_saved_liters: float = 0.0
