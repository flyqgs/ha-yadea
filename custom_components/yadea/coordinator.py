"""Data update coordinator for the Yadea integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import YadeaApi, YadeaAuthError, YadeaError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

LOGGER = logging.getLogger(__package__)


class YadeaCoordinator(DataUpdateCoordinator[dict]):
    """Poll the Yadea cloud for all bound vehicles."""

    api: YadeaApi

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: YadeaApi,
    ) -> None:
        interval = timedelta(
            seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=interval,
            config_entry=entry,  # required since HA 2025.11
        )
        self.entry = entry
        self.api = api

    async def _async_update_data(self) -> dict:
        try:
            return await self.api.get_bike_data()
        except YadeaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except YadeaError as err:
            raise UpdateFailed(str(err)) from err
