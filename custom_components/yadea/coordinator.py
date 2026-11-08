"""Data update coordinator for the Yadea integration.

两档刷新：
- 车辆状态（电量/续航/上报时间）：按 scan_interval（默认 60s）轮询
- 轨迹/里程统计：按 track_interval（默认 300s）刷新一次，其余轮询复用缓存
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import YadeaApi, YadeaAuthError, YadeaError
from .const import (
    CONF_AUTO_RELOGIN,
    CONF_SCAN_INTERVAL,
    CONF_TRACK_INTERVAL,
    DEFAULT_AUTO_RELOGIN,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TRACK_INTERVAL,
    DOMAIN,
)

LOGGER = logging.getLogger(__package__)


class YadeaCoordinator(DataUpdateCoordinator[dict]):
    """Poll the Yadea cloud for all bound vehicles."""

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
        self._track_interval = int(
            entry.options.get(CONF_TRACK_INTERVAL, DEFAULT_TRACK_INTERVAL)
        )
        self._auto_relogin = bool(
            entry.options.get(CONF_AUTO_RELOGIN, DEFAULT_AUTO_RELOGIN)
        )
        self._track_cache: dict[str, dict] = {}
        self._track_ts: float = 0.0

    @property
    def track_interval(self) -> int:
        return self._track_interval

    async def _async_update_data(self) -> dict:
        try:
            data = await self.api.get_status_data()
        except YadeaAuthError as err:
            if not await self._try_relogin():
                raise ConfigEntryAuthFailed(str(err)) from err
            data = await self.api.get_status_data()
        except YadeaError as err:
            raise UpdateFailed(str(err)) from err

        # 轨迹统计刷新节奏与状态轮询解耦
        if time.monotonic() - self._track_ts >= self._track_interval:
            await self._refresh_tracks(data)

        for vin, extra in (data.get("extras") or {}).items():
            cached = self._track_cache.get(vin)
            if cached is not None:
                extra["track"] = cached
        return data

    async def _refresh_tracks(self, data: dict) -> None:
        """拉取每台车的轨迹/里程统计；失败时保留上一次缓存。"""
        refreshed = False
        for bike in data.get("bikes") or []:
            vin = str(bike.get("vin") or "")
            if not vin:
                continue
            try:
                smart_type = int(bike.get("bikeInfoSmartType") or 2)
            except (TypeError, ValueError):
                smart_type = 2
            try:
                self._track_cache[vin] = await self.api.get_track_data(vin, smart_type)
                refreshed = True
            except YadeaAuthError:
                raise
            except YadeaError as err:
                LOGGER.debug("track refresh failed for %s: %s", vin, err)
        if refreshed:
            self._track_ts = time.monotonic()

    async def _try_relogin(self) -> bool:
        """单会话账号下重登会顶掉手机 App，默认关闭，需在选项里显式开启。"""
        if not self._auto_relogin:
            LOGGER.warning(
                "雅迪 token 已失效。为避免把手机 App 顶下线，集成不会自动重登；"
                "请在「设置 → 设备与服务」里重新授权雅迪智行"
            )
            return False
        try:
            await self.api.login()
        except YadeaError as err:
            LOGGER.error("雅迪重新登录失败: %s", err)
            return False
        self._track_ts = 0.0
        return True

    def invalidate_tracks(self) -> None:
        """Force a track refresh on the next poll."""
        self._track_ts = 0.0
