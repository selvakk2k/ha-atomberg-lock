"""Config flow and options flow for Atomberg Smart Lock integration."""

from __future__ import annotations

import logging
from typing import Any
import urllib.parse

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from pyatomberg.auth.cognito import CognitoOAuthHandler
from pyatomberg.const import COGNITO_REDIRECT_URI
from pyatomberg.lock.cloud_sync import AtombergCloudSync

from .const import (
    CONF_MAC,
    CONF_MASTER_KEY,
    CONF_PAIRING_METHOD,
    CONF_SALT,
    CONF_SLOT_MAPPINGS,
    DEFAULT_NAME,
    DOMAIN,
    METHOD_CLOUD,
    METHOD_MANUAL,
)

_LOGGER = logging.getLogger(__name__)


class AtombergLockConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Atomberg Smart Lock."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._selected_mac: str | None = None
        self._oauth_handler: CognitoOAuthHandler | None = None
        self._pending_verifier: str | None = None
        self._auth_url: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose between Cloud Account Sync or Manual Key Entry."""
        return self.async_show_menu(
            step_id="user",
            menu_options=[METHOD_CLOUD, METHOD_MANUAL],
        )

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Import lock credentials from Atomberg cloud account."""
        errors: dict[str, str] = {}

        session = async_get_clientsession(self.hass)
        if self._oauth_handler is None:
            self._oauth_handler = CognitoOAuthHandler(session=session)
            auth_url, _, verifier = self._oauth_handler.get_authorization_url(identity_provider=None)
            self._auth_url = auth_url
            self._pending_verifier = verifier

        if user_input is not None:
            raw_input = user_input.get("auth_code", "").strip()
            auth_token = raw_input

            # Check if user passed redirected URL or authorization code
            if "code=" in raw_input or ("-" in raw_input and len(raw_input) < 60):
                code = raw_input
                if "code=" in raw_input:
                    parsed = urllib.parse.urlparse(raw_input)
                    qs = urllib.parse.parse_qs(parsed.query)
                    if "code" in qs:
                        code = qs["code"][0]
                    else:
                        for part in raw_input.split("&"):
                            if part.startswith("code=") or "code=" in part:
                                code = part.split("code=")[-1]
                                break

                try:
                    tokens = await self._oauth_handler.exchange_code_for_tokens(
                        code=code,
                        code_verifier=self._pending_verifier,
                        redirect_uri=COGNITO_REDIRECT_URI,
                    )
                    auth_token = tokens.get("access_token") or tokens.get("id_token", "")
                except Exception as err:
                    _LOGGER.exception("Token exchange failed: %s", err)
                    errors["base"] = "cloud_sync_failed"

            if not errors and auth_token:
                sync_client = AtombergCloudSync(access_token=auth_token, session=session)
                try:
                    locks = await sync_client.fetch_account_locks()
                    if not locks:
                        errors["base"] = "no_locks_found"
                    else:
                        first_lock = locks[0]
                        await self.async_set_unique_id(first_lock.mac_address)
                        self._abort_if_unique_id_configured()

                        title = first_lock.name or f"Atomberg Lock ({first_lock.mac_address})"
                        return self.async_create_entry(
                            title=title,
                            data={
                                CONF_MAC: first_lock.mac_address,
                                CONF_MASTER_KEY: first_lock.master_key_hex,
                                CONF_SALT: first_lock.salt_hex,
                                CONF_PAIRING_METHOD: METHOD_CLOUD,
                                "name": first_lock.name or "Atomberg Smart Lock",
                                "model": first_lock.model or "smart_lock",
                            },
                        )
                except Exception as err:
                    _LOGGER.exception("Cloud sync failed: %s", err)
                    errors["base"] = "cloud_sync_failed"

        schema = vol.Schema({
            vol.Required("auth_code"): str,
        })
        return self.async_show_form(
            step_id="cloud",
            data_schema=schema,
            errors=errors,
            description_placeholders={"auth_url": self._auth_url or ""},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manual key entry for existing users or btsnoop log migrators."""
        errors: dict[str, str] = {}

        if user_input is not None:
            mac = user_input[CONF_MAC].strip().upper()
            await self.async_set_unique_id(mac)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"Atomberg Lock ({mac})",
                data={
                    CONF_MAC: mac,
                    CONF_MASTER_KEY: user_input[CONF_MASTER_KEY].strip(),
                    CONF_SALT: user_input[CONF_SALT].strip(),
                    CONF_PAIRING_METHOD: METHOD_MANUAL,
                },
            )

        schema = vol.Schema({
            vol.Required(CONF_MAC): str,
            vol.Required(CONF_MASTER_KEY): str,
            vol.Required(CONF_SALT): str,
        })
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return AtombergLockOptionsFlow(config_entry)


class AtombergLockOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Atomberg Lock (e.g. user/slot mappings)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._selected_slot: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Choose which slot to name or configure."""
        raw_mappings = dict(self._config_entry.options.get(CONF_SLOT_MAPPINGS, {}))
        current_mappings = {int(k): str(v) for k, v in raw_mappings.items() if str(k).isdigit()}

        # Retrieve discovered slots from coordinator if running
        discovered: dict[int, str] = {}
        coordinator = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id)
        if coordinator and hasattr(coordinator, "discovered_slots"):
            discovered = dict(coordinator.discovered_slots)

        known_slots = set(current_mappings.keys()) | set(discovered.keys())
        if not known_slots:
            known_slots = {14}

        if user_input is not None:
            self._selected_slot = user_input.get("slot_id")
            return await self.async_step_edit_slot()

        # Build dropdown options
        select_options: list[selector.SelectOptionDict] = []
        for slot_num in sorted(known_slots):
            cred_type = discovered.get(slot_num, "Enrolled Credential")
            curr_name = current_mappings.get(slot_num)
            if curr_name:
                label = f"Slot {slot_num}: {curr_name} ({cred_type})"
            else:
                label = f"Slot {slot_num}: {cred_type}"
            select_options.append(selector.SelectOptionDict(value=str(slot_num), label=label))

        select_options.append(
            selector.SelectOptionDict(value="custom", label="Add Custom Slot Number...")
        )

        default_slot = str(sorted(known_slots)[0])

        schema = vol.Schema({
            vol.Required("slot_id"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=select_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        schema = self.add_suggested_values_to_schema(schema, {"slot_id": default_slot})

        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_edit_slot(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Assign or update the name for the selected slot."""
        errors: dict[str, str] = {}
        raw_mappings = dict(self._config_entry.options.get(CONF_SLOT_MAPPINGS, {}))
        current_mappings = {int(k): str(v) for k, v in raw_mappings.items() if str(k).isdigit()}

        is_custom = self._selected_slot == "custom"
        target_slot: int | None = None
        if not is_custom and self._selected_slot is not None:
            try:
                target_slot = int(self._selected_slot)
            except ValueError:
                pass

        if user_input is not None:
            name = user_input.get("name", "").strip()
            if is_custom:
                raw_slot = user_input.get("slot_id")
                try:
                    target_slot = int(raw_slot) if raw_slot is not None else None
                except (ValueError, TypeError):
                    target_slot = None

            if target_slot is not None and target_slot > 0:
                updated_mappings = dict(current_mappings)
                if name:
                    updated_mappings[target_slot] = name
                else:
                    updated_mappings.pop(target_slot, None)

                return self.async_create_entry(
                    title="",
                    data={CONF_SLOT_MAPPINGS: {str(k): v for k, v in updated_mappings.items()}},
                )
            errors["base"] = "invalid_slot"

        fields: dict[Any, Any] = {}
        suggested: dict[str, Any] = {}

        if is_custom:
            fields[vol.Required("slot_id")] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=65534,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
            fields[vol.Optional("name", default="")] = selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            )
            slot_title_placeholder = "New"
        else:
            fields[vol.Optional("name", default="")] = selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            )
            if target_slot is not None and target_slot in current_mappings:
                suggested["name"] = current_mappings[target_slot]
            slot_title_placeholder = str(target_slot)

        schema = vol.Schema(fields)
        if suggested:
            schema = self.add_suggested_values_to_schema(schema, suggested)

        return self.async_show_form(
            step_id="edit_slot",
            data_schema=schema,
            errors=errors,
            description_placeholders={"slot_id": slot_title_placeholder},
        )
