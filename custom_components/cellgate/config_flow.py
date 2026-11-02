"""Configure CellGate from the Home Assistant UI."""

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .api import CellGateAuthError, CellGateClient, CellGateError
from .const import DOMAIN, CONF_ENABLE_CALLS, CONF_BACKEND_URL, CONF_BACKEND_TOKEN
from .intercom import check_backend, IntercomError, IntercomAuthError

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class CellGateConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return CellGateOptionsFlow()

    async def _validate(self, data):
        client = CellGateClient(
            async_get_clientsession(self.hass), data[CONF_USERNAME], data[CONF_PASSWORD]
        )
        await client.login()
        gates = await client.discover()
        return client.account_key, gates

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                user_id, gates = await self._validate(user_input)
                if not gates:
                    errors["base"] = "no_gates"
                else:
                    await self.async_set_unique_id(str(user_id))
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title="CellGate", data=user_input)
            except CellGateAuthError:
                errors["base"] = "invalid_auth"
            except CellGateError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                user_id, _ = await self._validate(user_input)
                await self.async_set_unique_id(str(user_id))
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates=user_input
                )
            except CellGateAuthError:
                errors["base"] = "invalid_auth"
            except CellGateError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=SCHEMA, errors=errors
        )


class CellGateOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        values = dict(self.config_entry.options)
        if user_input is not None:
            values.update(user_input)
            if values.get(CONF_ENABLE_CALLS):
                try:
                    token = values.get(CONF_BACKEND_TOKEN, "")
                    if len(token) < 32:
                        raise IntercomAuthError("Backend token required")
                    values[CONF_BACKEND_URL] = await check_backend(
                        async_get_clientsession(self.hass),
                        values.get(CONF_BACKEND_URL, ""),
                        token,
                    )
                except IntercomAuthError:
                    errors["base"] = "backend_auth"
                except IntercomError:
                    errors["base"] = "backend_unavailable"
            if not errors:
                return self.async_create_entry(title="", data=values)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ENABLE_CALLS, default=values.get(CONF_ENABLE_CALLS, False)
                ): bool,
                vol.Optional(
                    CONF_BACKEND_URL, default=values.get(CONF_BACKEND_URL, "")
                ): str,
                vol.Optional(
                    CONF_BACKEND_TOKEN, default=values.get(CONF_BACKEND_TOKEN, "")
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
