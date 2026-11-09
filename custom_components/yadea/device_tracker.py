"""Device tracker for the Yadea integration.

雅迪云端没有实时定位接口：车辆休眠时不上报坐标，`bikeRealStatus/getVehRealStatus`
在非骑行状态返回空。因此这里给出的是**最近一次骑行结束时的停车位置**——
取最近一段轨迹的最后一个 GPS 点（骑行中随轨迹刷新，静止时即停车点）。

轨迹按账号归属：共享账号查不到轨迹，本实体也不会有状态。
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME, STATE_NOT_HOME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.location import distance

from .const import DOMAIN
from .coordinator import YadeaCoordinator

LOGGER = logging.getLogger(__package__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one tracker per bound vehicle."""
    coordinator: YadeaCoordinator = entry.runtime_data
    bikes = (coordinator.data or {}).get("bikes", [])
    async_add_entities(YadeaLastLocationTracker(coordinator, bike) for bike in bikes)


class YadeaLastLocationTracker(CoordinatorEntity[YadeaCoordinator], TrackerEntity):
    """最近一次骑行的停车位置（非实时定位）。"""

    _attr_has_entity_name = True
    _attr_translation_key = "last_location"
    _attr_icon = "mdi:map-marker-radius"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: YadeaCoordinator, bike: dict) -> None:
        super().__init__(coordinator)
        self.vin = str(bike.get("vin"))
        self._bike = bike
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{self.vin}_last_location"
        self._attr_entity_picture_url = bike.get("imgUrl") or None

    # ---------- 基础信息 ----------

    @property
    def _current(self) -> dict:
        for bike in (self.coordinator.data or {}).get("bikes", []):
            if str(bike.get("vin")) == self.vin:
                return bike
        return self._bike

    @property
    def _location(self) -> dict[str, Any]:
        extras = (self.coordinator.data or {}).get("extras", {}).get(self.vin, {})
        return (extras.get("track") or {}).get("location") or {}

    @property
    def device_info(self) -> DeviceInfo:
        bike = self._current
        return DeviceInfo(
            identifiers={(DOMAIN, self.vin)},
            name=bike.get("bikeNickName") or bike.get("modelName") or f"Yadea {self.vin}",
            manufacturer="Yadea 雅迪",
            model=bike.get("modelName"),
            serial_number=self.vin,
        )

    @property
    def available(self) -> bool:
        return bool(self._location) and any(
            str(b.get("vin")) == self.vin
            for b in (self.coordinator.data or {}).get("bikes", [])
        )

    # ---------- 位置 ----------

    @property
    def latitude(self) -> float | None:
        value = self._location.get("latitude")
        return float(value) if value is not None else None

    @property
    def longitude(self) -> float | None:
        value = self._location.get("longitude")
        return float(value) if value is not None else None

    @property
    def location_accuracy(self) -> int:
        """云端轨迹点没有精度信息，给 0 表示未知（地图不做精度圈）。"""
        return 0

    @property
    def state(self) -> str | None:
        """在家 / 不在家：按 zone.home 半径判断（location_name 在 2026.11 已废弃）。"""
        loc = self._location
        if not loc or self.latitude is None or self.longitude is None:
            return None
        home = self.hass.states.get("zone.home")
        if home is None:
            return STATE_NOT_HOME
        try:
            home_lat = float(home.attributes["latitude"])
            home_lon = float(home.attributes["longitude"])
            radius = float(home.attributes.get("radius") or 100)
        except (KeyError, TypeError, ValueError):
            return STATE_NOT_HOME
        try:
            dist = distance(self.latitude, self.longitude, home_lat, home_lon)
        except ValueError:
            return STATE_NOT_HOME
        return STATE_HOME if dist <= radius else STATE_NOT_HOME

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        loc = self._location
        if not loc:
            return {}
        return {
            "address": loc.get("address"),
            "location_time": loc.get("time"),
            "ride_id": loc.get("ride_id"),
            "note": "云端无实时定位，此处为最近一次骑行结束时的停车位置",
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
