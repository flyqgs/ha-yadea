"""The Yadea (雅迪智行) integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import YadeaApi, YadeaError
from .const import CONF_ACCOUNT, CONF_PASSWORD, DOMAIN, PLATFORMS
from .coordinator import YadeaCoordinator

LOGGER = logging.getLogger(__package__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Yadea from a config entry."""
    session = async_get_clientsession(hass)
    api = YadeaApi(entry.data[CONF_ACCOUNT], entry.data[CONF_PASSWORD], session)

    try:
        await api.login()
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


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
