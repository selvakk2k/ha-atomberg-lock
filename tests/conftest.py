"""Pytest configuration and Home Assistant stubs for Atomberg Smart Lock."""

import sys
import types
from enum import IntFlag
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Add project root and custom_components to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Stub bleak if not installed
if "bleak" not in sys.modules:
    bleak = types.ModuleType("bleak")
    bleak.BleakClient = MagicMock
    bleak_backends = types.ModuleType("bleak.backends")
    bleak_device = types.ModuleType("bleak.backends.device")
    bleak_device.BLEDevice = MagicMock

    sys.modules["bleak"] = bleak
    sys.modules["bleak.backends"] = bleak_backends
    sys.modules["bleak.backends.device"] = bleak_device

if "bleak_retry_connector" not in sys.modules:
    brc = types.ModuleType("bleak_retry_connector")
    brc.BleakClientWithServiceCache = MagicMock
    brc.establish_connection = AsyncMock()
    sys.modules["bleak_retry_connector"] = brc

# Set up Home Assistant mock stubs for standalone testing
from tests.ha_stub import setup_ha_stubs  # noqa: E402

setup_ha_stubs()

import homeassistant.components.lock  # noqa: E402


class LockEntityFeature(IntFlag):
    OPEN = 1


homeassistant.components.lock.LockEntityFeature = LockEntityFeature


class LockEntity:
    pass


homeassistant.components.lock.LockEntity = LockEntity

import homeassistant.components.button  # noqa: E402


class ButtonEntity:
    pass


homeassistant.components.button.ButtonEntity = ButtonEntity
