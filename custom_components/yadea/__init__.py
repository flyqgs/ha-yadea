"""The Yadea (雅迪智行) integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import YadeaApi, YadeaAuthError, YadeaError
from .const import CONF_ACCOUNT, CONF_PASSWORD, DOMAIN, PLATFORMS
from .coordinator import YadeaCoordinator

LOGGER = logging.getLogger(__package__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Yadea from a config entry."""
    session = async_get_clientsession(hass)
    api = YadeaApi(entry.data[CONF_ACCOUNT], entry.data[CONF_PASSWORD], session)

    # 雅迪单会话：任何新登录都会让手机 App 的 token 失效。把 token 存到
    # .storage，HA 重启/重载时直接复用，只有 token 真失效才需要重新授权。
    store = Store(hass, version=1, key=f"{DOMAIN}_token_{entry.entry_id}")
    stored = await store.async_load()
    if isinstance(stored, dict) and stored.get("token"):
        api.token = stored["token"]
    api.token_callback = _make_token_saver(store)

    try:
        await api.get_bikes()  # validate cached token
    except YadeaAuthError as err:
        # 不自动重登（会顶掉手机 App），交给 reauth 流程让用户确认
        raise ConfigEntryAuthFailed(str(err)) from err
    except YadeaError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = YadeaCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the cached token when the entry is deleted."""
    store = Store(hass, version=1, key=f"{DOMAIN}_token_{entry.entry_id}")
    await store.async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _make_token_saver(store: Store):
    """Return an async callback persisting the token after each login."""

    async def _save(token: str) -> None:
        await store.async_save({"token": token})

    return _save
