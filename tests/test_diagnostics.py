"""Unit tests for Atomberg Smart Lock diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.atomberg_lock.const import (
    CONF_MAC,
    CONF_MASTER_KEY,
    CONF_SALT,
    DOMAIN,
)
from custom_components.atomberg_lock.diagnostics import (
    async_get_config_entry_diagnostics,
)


@pytest.mark.asyncio
async def test_diagnostics_redacts_sensitive_keys():
    """Test diagnostics redacts master key, salt, MAC, and tokens."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "atomberg_entry_123"
    entry.title = "Front Door Atomberg Lock"
    entry.data = {
        CONF_MAC: "AA:BB:CC:11:22:33",
        CONF_MASTER_KEY: "super_secret_master_key_12345",
        CONF_SALT: "random_salt_bytes_abcdef",
        "lock_salt": "legacy_salt",
        "adminAuthCode": "123456",
        "pin": "9876",
        "device_name": "Front Door",
    }
    entry.options = {
        "refresh_token": "secret_refresh_token_xyz",
        "scan_interval": 30,
    }

    mock_coordinator = MagicMock()
    mock_lock_device = MagicMock()
    mock_lock_device.is_connected = True
    mock_coordinator._lock_device = mock_lock_device
    mock_coordinator.is_connected = True
    mock_coordinator.data = {
        "battery": 92,
        "lock_state": "locked",
        "master_key": "must_be_redacted",
    }
    mock_coordinator.last_event = {
        "event_type": "unlock",
        "user": "Alice",
        "pin_code": "1234",
    }

    entry.runtime_data = mock_coordinator
    hass.data = {DOMAIN: {entry.entry_id: mock_coordinator}}

    diag = await async_get_config_entry_diagnostics(hass, entry)

    # Check entry data redactions
    entry_data = diag["entry"]["data"]
    assert entry_data[CONF_MASTER_KEY] == "**REDACTED**"
    assert entry_data[CONF_SALT] == "**REDACTED**"
    assert entry_data[CONF_MAC] == "**REDACTED**"
    assert entry_data["adminAuthCode"] == "**REDACTED**"
    assert entry_data["pin"] == "**REDACTED**"
    assert entry_data["device_name"] == "Front Door"

    # Check entry options redactions
    entry_opts = diag["entry"]["options"]
    assert entry_opts["refresh_token"] == "**REDACTED**"
    assert entry_opts["scan_interval"] == 30

    # Check coordinator data redactions
    coord = diag["coordinator"]
    assert coord["connected"] is True
    assert coord["data"]["battery"] == 92
    assert coord["data"]["master_key"] == "**REDACTED**"
    assert coord["last_event"]["pin_code"] == "**REDACTED**"
    assert coord["last_event"]["user"] == "Alice"


@pytest.mark.asyncio
async def test_diagnostics_without_coordinator():
    """Test diagnostics works cleanly before coordinator is loaded."""
    hass = MagicMock()
    hass.data = {}
    entry = MagicMock()
    entry.entry_id = "unloaded_entry"
    entry.title = "Unloaded Lock"
    entry.data = {
        CONF_MAC: "11:22:33:44:55:66",
        CONF_MASTER_KEY: "secret",
    }
    entry.options = {}
    entry.runtime_data = None

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["entry"]["title"] == "Unloaded Lock"
    assert diag["entry"]["data"][CONF_MAC] == "**REDACTED**"
    assert diag["entry"]["data"][CONF_MASTER_KEY] == "**REDACTED**"
    assert diag["coordinator"]["connected"] is None
    assert diag["coordinator"]["data"] == {}

