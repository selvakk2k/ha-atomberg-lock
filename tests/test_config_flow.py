"""Unit tests for Atomberg Smart Lock config flow."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.atomberg_lock.config_flow import AtombergLockConfigFlow
from custom_components.atomberg_lock.const import (
    CONF_MAC,
    CONF_MASTER_KEY,
    CONF_PAIRING_METHOD,
    CONF_SALT,
    METHOD_CLOUD,
    METHOD_MANUAL,
)


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant core instance."""
    hass = MagicMock()
    hass.data = {}
    return hass


@pytest.mark.asyncio
async def test_step_user_shows_menu(mock_hass):
    """Test step user presents pairing choices (Cloud, Cloud-Assisted Local, Manual)."""
    flow = AtombergLockConfigFlow()
    flow.hass = mock_hass

    result = await flow.async_step_user()
    assert result["type"] == "menu"
    assert result["step_id"] == "user"
    assert METHOD_CLOUD in result["menu_options"]
    assert "cloud_assisted_local" in result["menu_options"]
    assert METHOD_MANUAL in result["menu_options"]


@pytest.mark.asyncio
async def test_step_manual_valid(mock_hass):
    """Test successful manual pairing creates config entry."""
    flow = AtombergLockConfigFlow()
    flow.hass = mock_hass

    valid_input = {
        CONF_MAC: "AA:BB:CC:11:22:33",
        CONF_MASTER_KEY: "0123456789abcdef0123456789abcdef",
        CONF_SALT: "aabbccdd",
    }

    result = await flow.async_step_manual(valid_input)
    assert result["type"] == "create_entry"
    assert result["title"] == "Atomberg Lock (AA:BB:CC:11:22:33)"
    assert result["data"][CONF_MAC] == "AA:BB:CC:11:22:33"
    assert result["data"][CONF_MASTER_KEY] == "0123456789abcdef0123456789abcdef"
    assert result["data"][CONF_SALT] == "aabbccdd"
    assert result["data"][CONF_PAIRING_METHOD] == METHOD_MANUAL


@pytest.mark.asyncio
async def test_step_manual_invalid_mac(mock_hass):
    """Test invalid MAC address format is rejected."""
    flow = AtombergLockConfigFlow()
    flow.hass = mock_hass

    invalid_input = {
        CONF_MAC: "INVALID_MAC",
        CONF_MASTER_KEY: "0123456789abcdef0123456789abcdef",
        CONF_SALT: "aabbccdd",
    }

    result = await flow.async_step_manual(invalid_input)
    assert result["type"] == "form"
    assert result["errors"][CONF_MAC] == "invalid_mac"


@pytest.mark.asyncio
async def test_step_manual_invalid_key(mock_hass):
    """Test invalid key length or hex characters is rejected."""
    flow = AtombergLockConfigFlow()
    flow.hass = mock_hass

    invalid_input = {
        CONF_MAC: "AA:BB:CC:11:22:33",
        CONF_MASTER_KEY: "too_short_key",
        CONF_SALT: "aabbccdd",
    }

    result = await flow.async_step_manual(invalid_input)
    assert result["type"] == "form"
    assert result["errors"][CONF_MASTER_KEY] == "invalid_key"


@pytest.mark.asyncio
async def test_step_manual_invalid_salt(mock_hass):
    """Test invalid salt length is rejected."""
    flow = AtombergLockConfigFlow()
    flow.hass = mock_hass

    invalid_input = {
        CONF_MAC: "AA:BB:CC:11:22:33",
        CONF_MASTER_KEY: "0123456789abcdef0123456789abcdef",
        CONF_SALT: "1234",  # needs 8 hex chars (4 bytes)
    }

    result = await flow.async_step_manual(invalid_input)
    assert result["type"] == "form"
    assert result["errors"][CONF_SALT] == "invalid_salt"

