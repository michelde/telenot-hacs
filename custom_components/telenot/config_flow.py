"""UI setup: validate identity with read-only protocol traffic."""

from __future__ import annotations

import homeassistant  # noqa: F401 -- initialize HA's voluptuous compatibility layer first
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import CONF_ALLOW_CONTROL, DEFAULT_PORT, DOMAIN
from .protocol.client import ConnectionLost, GMSClient


class TelenotConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})
            client = GMSClient(host, port, discover=False)
            try:
                await client.start()
                ident = client.state.identity
                await self.async_set_unique_id(ident)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"TELENOT {ident}", data={
                    CONF_HOST: host, CONF_PORT: port, "identity": ident,
                })
            except (ConnectionLost, OSError):
                errors["base"] = "cannot_connect"
            finally:
                await client.stop()
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_HOST): cv.string,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
        }), errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            host, port = user_input[CONF_HOST].strip(), user_input[CONF_PORT]
            client = GMSClient(host, port, identity=entry.data["identity"], discover=False)
            # Release the old connection before validating the new gateway endpoint.
            was_loaded = entry.state is config_entries.ConfigEntryState.LOADED
            if was_loaded:
                if not await self.hass.config_entries.async_unload(entry.entry_id):
                    errors["base"] = "cannot_connect"
                    return self.async_show_form(step_id="reconfigure", errors=errors,
                                                data_schema=self._endpoint_schema(entry.data))
            try:
                await client.start()
                await client.stop()
                return self.async_update_reload_and_abort(entry, data_updates={CONF_HOST: host, CONF_PORT: port})
            except (ConnectionLost, OSError):
                errors["base"] = "cannot_connect"
            finally:
                await client.stop()
                if errors and was_loaded:
                    await self.hass.config_entries.async_setup(entry.entry_id)
        return self.async_show_form(step_id="reconfigure", errors=errors,
                                    data_schema=self._endpoint_schema(entry.data))

    @staticmethod
    def _endpoint_schema(data):
        return vol.Schema({vol.Required(CONF_HOST, default=data[CONF_HOST]): cv.string,
                           vol.Required(CONF_PORT, default=data[CONF_PORT]): cv.port})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return TelenotOptionsFlow()


class TelenotOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Optional(CONF_ALLOW_CONTROL, default=self.config_entry.options.get(CONF_ALLOW_CONTROL, False)): bool,
        }))
