"""Config flow to configure the Yadea integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import YadeaApi, YadeaError
from .const import (
    CONF_ACCOUNT,
    CONF_AUTO_RELOGIN,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TRACK_INTERVAL,
    DEFAULT_AUTO_RELOGIN,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TRACK_INTERVAL,
    DOMAIN,
)

LOGGER = logging.getLogger(__package__)

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
        vol.Required(CONF_TRACK_INTERVAL, default=DEFAULT_TRACK_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=60, max=3600)
        ),
        vol.Required(CONF_AUTO_RELOGIN, default=DEFAULT_AUTO_RELOGIN): bool,
    }
)

# 换账号：留空表示不修改。轨迹/里程只在车主账号下可见，共享账号查不到，
# 所以这里提供一个不删条目的换号入口（entry_id 不变，实体 ID 也不会漂移）。
ACCOUNT_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ACCOUNT, default=""): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
    }
)


class YadeaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Yadea config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

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
                return self.async_create_entry(
                    title=f"雅迪智行 ({user_input[CONF_ACCOUNT]})",
                    data=user_input,
                    description=f"发现 {len(bikes)} 台车辆",
                )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    # ---------- 重新授权（token 被手机 App 顶掉时） ----------

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._reauth_entry
        if entry is None:
            return self.async_abort(reason="reauth_failed")
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            password = user_input.get(CONF_PASSWORD) or entry.data[CONF_PASSWORD]
            api = YadeaApi(entry.data[CONF_ACCOUNT], password, session)
            try:
                await api.login()
            except YadeaError as err:
                errors["base"] = (
                    "invalid_auth"
                    if "密码" in str(err) or "账号" in str(err)
                    else "cannot_connect"
                )
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            else:
                await _save_token(self.hass, entry, api.token or "")
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_PASSWORD: password},
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Optional(CONF_PASSWORD): str}),
            description_placeholders={"account": entry.data[CONF_ACCOUNT]},
            errors=errors,
        )

    # ---------- 修改账号密码 ----------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = YadeaApi(user_input[CONF_ACCOUNT], user_input[CONF_PASSWORD], session)
            try:
                await api.login()
                bikes = await api.get_bikes()
            except YadeaError as err:
                errors["base"] = (
                    "invalid_auth"
                    if "密码" in str(err) or "账号" in str(err)
                    else "cannot_connect"
                )
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            else:
                await _save_token(self.hass, entry, api.token or "")
                return self.async_update_reload_and_abort(
                    entry,
                    data=user_input,
                    reason="reconfigure_successful",
                    description_placeholders={"count": str(len(bikes))},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or entry.data
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return YadeaOptionsFlow(config_entry)


class YadeaOptionsFlow(config_entries.OptionsFlow):
    """Options flow: poll intervals + re-login policy."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            account = str(user_input.pop(CONF_ACCOUNT, "") or "").strip()
            password = str(user_input.pop(CONF_PASSWORD, "") or "")
            if account or password:
                new_account = account or self.entry.data[CONF_ACCOUNT]
                new_password = password or self.entry.data[CONF_PASSWORD]
                if (new_account, new_password) != (
                    self.entry.data[CONF_ACCOUNT],
                    self.entry.data[CONF_PASSWORD],
                ):
                    session = async_get_clientsession(self.hass)
                    api = YadeaApi(new_account, new_password, session)
                    try:
                        await api.login()
                        await api.get_bikes()
                    except YadeaError as err:
                        errors["base"] = (
                            "invalid_auth"
                            if "密码" in str(err) or "账号" in str(err)
                            else "cannot_connect"
                        )
                    except aiohttp.ClientError:
                        errors["base"] = "cannot_connect"
                    else:
                        await _save_token(self.hass, self.entry, api.token or "")
                        self.hass.config_entries.async_update_entry(
                            self.entry,
                            data={
                                **self.entry.data,
                                CONF_ACCOUNT: new_account,
                                CONF_PASSWORD: new_password,
                            },
                            title=f"雅迪智行 ({new_account})",
                        )
            if not errors:
                return self.async_create_entry(title="", data=user_input)

        schema = self.add_suggested_values_to_schema(OPTIONS_SCHEMA, self.entry.options)
        return self.async_show_form(
            step_id="init",
            data_schema=schema.extend(ACCOUNT_SCHEMA.schema),
            errors=errors,
        )


async def _save_token(hass, entry: ConfigEntry, token: str) -> None:
    """Persist the freshly issued token for the entry."""
    if not token:
        return
    store = Store(hass, version=1, key=f"{DOMAIN}_token_{entry.entry_id}")
    await store.async_save({"token": token})
