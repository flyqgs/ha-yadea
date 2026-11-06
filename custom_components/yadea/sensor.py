"""Sensors for the Yadea integration.

每台车一个设备，实体来自车辆列表快照（soc1 电量、remMileage 剩余里程、
updateTime 上报时间、iotExpireDays 物联卡剩余天数）+ 每日用电记录。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .coordinator import YadeaCoordinator

LOGGER = logging.getLogger(__package__)

CN_TZ = timezone(timedelta(hours=8))  # 雅迪云端时间为中国标准时间


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_cn_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CN_TZ)
        return dt
    except ValueError:
        return None


class YadeaEntity(CoordinatorEntity[YadeaCoordinator], SensorEntity):
    """Base entity for one Yadea bike."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator)
        self.vin = vin
        self._bike = bike

    @property
    def _current(self) -> dict:
        bikes = (self.coordinator.data or {}).get("bikes", [])
        for bike in bikes:
            if str(bike.get("vin")) == self.vin:
                return bike
        return self._bike

    @property
    def _extras(self) -> dict:
        return (self.coordinator.data or {}).get("extras", {}).get(self.vin, {})

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
        return self.coordinator.last_update_success and any(
            str(b.get("vin")) == self.vin
            for b in (self.coordinator.data or {}).get("bikes", [])
        )


class YadeaBatterySensor(YadeaEntity):
    """Battery SOC (%%)."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "battery"

    @property
    def native_value(self) -> int | None:
        soc = self._current.get("soc1")
        return int(soc) if soc is not None else None


class YadeaRangeSensor(YadeaEntity):
    """Remaining range (km)."""

    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_translation_key = "range"

    @property
    def native_value(self) -> float | None:
        return _to_float(self._current.get("remMileage"))


class YadeaLastReportSensor(YadeaEntity):
    """Last cloud report time."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "last_report"

    @property
    def native_value(self) -> datetime | None:
        dt = _parse_cn_time(self._current.get("updateTime"))
        return dt_util.as_utc(dt) if dt else None


class YadeaBatteryCountSensor(YadeaEntity):
    """Number of batteries."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "battery_count"

    @property
    def native_value(self) -> int | None:
        value = self._current.get("batteryCount")
        return int(value) if value is not None else None


class YadeaIotDaysSensor(YadeaEntity):
    """IoT SIM card days remaining."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_translation_key = "iot_days"

    @property
    def native_value(self) -> int | None:
        value = self._current.get("iotExpireDays")
        return int(value) if value is not None else None


class YadeaMonthMileageSensor(YadeaEntity):
    """Current month mileage (km), best effort."""

    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_translation_key = "month_mileage"

    @property
    def native_value(self) -> float | None:
        return self._extras.get("month_mileage")


class YadeaDailyPowerSensor(YadeaEntity):
    """Latest daily power record (charge / consume, kWh)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, vin: str, bike: dict, kind: str) -> None:
        super().__init__(coordinator, vin, bike)
        self._kind = kind  # "charge" | "consume"
        self._attr_translation_key = (
            "daily_charge" if kind == "charge" else "daily_consume"
        )

    @property
    def native_value(self) -> float | None:
        power = self._extras.get("power") or {}
        key = "powerCharge" if self._kind == "charge" else "powerConsume"
        return _to_float(power.get(key))

    @property
    def extra_state_attributes(self) -> dict:
        power = self._extras.get("power") or {}
        return {"record_date": power.get("recordDate")}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yadea sensors from a config entry."""
    coordinator: YadeaCoordinator = entry.runtime_data
    data = coordinator.data or {}
    bikes = data.get("bikes", [])
    if not bikes:
        LOGGER.warning("雅迪账号下没有绑定车辆")
        return

    entities: list[YadeaEntity] = []
    for bike in bikes:
        vin = str(bike.get("vin", ""))
        if not vin:
            continue
        entities.extend(
            [
                YadeaBatterySensor(coordinator, vin, bike),
                YadeaRangeSensor(coordinator, vin, bike),
                YadeaLastReportSensor(coordinator, vin, bike),
                YadeaBatteryCountSensor(coordinator, vin, bike),
                YadeaIotDaysSensor(coordinator, vin, bike),
                YadeaMonthMileageSensor(coordinator, vin, bike),
                YadeaDailyPowerSensor(coordinator, vin, bike, "charge"),
                YadeaDailyPowerSensor(coordinator, vin, bike, "consume"),
            ]
        )
    async_add_entities(entities)
