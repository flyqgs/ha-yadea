"""Config flow to configure the Yadea integration."""
from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import YadeaApi, YadeaError
from .const import (
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCOUNT): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=30, max=600)
        ),
    }
)


class YadeaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Yadea config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = YadeaApi(user_input[CONF_ACCOUNT], user_input[CONF_PASSWORD], session)
            try:
                await api.login()
                bikes = await api.get_bikes()
            except YadeaError as err:
                errors["base"] = "cannot_connect"
                # 区分账号错误，提示更友好
                if "密码" in str(err) or "未注册" in str(err) or "账号" in str(err):
                    errors["base"] = "invalid_auth"
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_ACCOUNT])
                self._abort_if_unique_id_configured()
                n = len(bikes)
                return self.async_create_entry(
                    title=f"雅迪智行 ({user_input[CONF_ACCOUNT]})",
                    data=user_input,
                    description=f"发现 {n} 台车辆",
                )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return YadeaOptionsFlow(config_entry)


class YadeaOptionsFlow(config_entries.OptionsFlow):
    """Options flow: scan interval."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.entry.options
            ),
        )
