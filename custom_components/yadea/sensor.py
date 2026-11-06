"""Sensors for the Yadea integration.

每台车一个设备，实体来自车辆列表快照（soc1 电量、remMileage 剩余里程、
updateTime 上报时间、iotExpireDays 物联卡剩余天数）+ 每日用电记录。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
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

    def __init__(
        self,
        coordinator: YadeaCoordinator,
        vin: str,
        bike: dict,
        entity_key: str,
    ) -> None:
        super().__init__(coordinator)
        self.vin = vin
        self._bike = bike
        # 全局唯一 ID：VIN 出厂唯一 + 实体类型，重装/重载后保持稳定，
        # 使实体可归入对应车辆设备并在 UI 中管理
        self._attr_unique_id = f"{vin}_{entity_key}"
        # 官方车辆图片（实体级，DeviceInfo 不支持 picture_url 参数）
        self._attr_entity_picture_url = bike.get("imgUrl") or None

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

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "battery")

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

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "range")

    @property
    def native_value(self) -> float | None:
        return _to_float(self._current.get("remMileage"))


class YadeaLastReportSensor(YadeaEntity):
    """Last cloud report time."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "last_report"

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "last_report")

    @property
    def native_value(self) -> datetime | None:
        dt = _parse_cn_time(self._current.get("updateTime"))
        return dt_util.as_utc(dt) if dt else None


class YadeaBatteryCountSensor(YadeaEntity):
    """Number of batteries."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "battery_count"

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "battery_count")

    @property
    def native_value(self) -> int | None:
        value = self._current.get("batteryCount")
        return int(value) if value is not None else None


class YadeaIotDaysSensor(YadeaEntity):
    """IoT SIM card days remaining."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_translation_key = "iot_days"

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "iot_days")

    @property
    def native_value(self) -> int | None:
        value = self._current.get("iotExpireDays")
        return int(value) if value is not None else None


class YadeaMonthMileageSensor(YadeaEntity):
    """Current month mileage (km), best effort."""

    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_translation_key = "month_mileage"

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "month_mileage")

    @property
    def native_value(self) -> float | None:
        return self._extras.get("month_mileage")


class YadeaDailyPowerSensor(YadeaEntity):
    """Latest daily power record (charge / consume, kWh)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, vin: str, bike: dict, kind: str) -> None:
        super().__init__(coordinator, vin, bike, f"daily_power_{kind}")
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


class YadeaIotExpireSensor(YadeaEntity):
    """IoT SIM card expiration date."""

    _attr_device_class = SensorDeviceClass.DATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "iot_expire"

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "iot_expire")

    @property
    def native_value(self) -> date | None:
        raw = self._current.get("iotCardExpireTime")
        if not raw:
            return None
        try:
            return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


class YadeaVehicleInfoSensor(YadeaEntity):
    """Vehicle profile diagnostic sensor with hardware/app info attributes."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:information-outline"
    _attr_translation_key = "vehicle_info"

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "vehicle_info")

    @property
    def native_value(self) -> str:
        bike = self._current
        return bike.get("modelName") or bike.get("bikeNickName") or self.vin

    @property
    def extra_state_attributes(self) -> dict:
        bike = self._current
        return {
            "model_no": bike.get("modelNo"),
            "material_code": bike.get("materialCode"),
            "color": bike.get("color"),
            "battery_type": bike.get("battType"),
            "bluetooth_name": bike.get("bluetoothName"),
            "mac_address": bike.get("mac"),
            "iccid": bike.get("iccid"),
            "iot_card_expire": bike.get("iotCardExpireTime"),
            "bound_at": bike.get("createTime"),
            "double_warranty": bike.get("doubleWarranty"),
            "lifetime_warranty_battery": bike.get("lifetimeWarrantyBattery"),
            "vehicle_image": bike.get("imgUrl"),
        }


class YadeaTotalMileageSensor(YadeaEntity):
    """Lifetime total mileage (km)."""

    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_translation_key = "total_mileage"

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "total_mileage")

    @property
    def native_value(self) -> float | None:
        return _to_float((self._extras.get("total") or {}).get("vehicleTotalMileage"))


class YadeaTotalCO2Sensor(YadeaEntity):
    """Lifetime CO2 emission saved (kg)."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "kg"
    _attr_icon = "mdi:leaf"
    _attr_translation_key = "total_co2"

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "total_co2")

    @property
    def native_value(self) -> float | None:
        return _to_float((self._extras.get("total") or {}).get("totalCO2"))

    @property
    def extra_state_attributes(self) -> dict:
        stats = self._extras.get("total") or {}
        return {"kg_per_km": stats.get("kgPerKm")}


class YadeaDailyMileageSensor(YadeaEntity):
    """Daily ride mileage over the last 7 days (diagnostic).

    Cloud ride records only exist when rides were tracked in the app;
    otherwise the list stays empty and the sensor reports unknown.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_icon = "mdi:calendar-clock"
    _attr_translation_key = "daily_mileage"

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "daily_mileage")

    @staticmethod
    def _extract(records: list[dict]) -> list[dict]:
        """Normalize server records into {date, km} pairs (best effort)."""
        days: list[dict] = []
        for r in records:
            if not isinstance(r, dict):
                continue
            day = km = None
            for key, value in r.items():
                lower = key.lower()
                if day is None and (
                    "start" in lower or "day" in lower or "date" in lower
                ):
                    day = value
                if km is None and "mileage" in lower:
                    km = _to_float(value)
            days.append({"date": day, "km": km})
        return days

    @property
    def native_value(self) -> float | None:
        records = self._extras.get("daily_mileage") or []
        if not records:
            return None
        total = sum(
            km for d in self._extract(records) if (km := d.get("km")) is not None
        )
        return round(total, 2)

    @property
    def extra_state_attributes(self) -> dict:
        records = self._extras.get("daily_mileage") or []
        return {"last_7_days": self._extract(records), "record_count": len(records)}


class YadeaWeeklyPowerSensor(YadeaEntity):
    """Rolling 7-day charge / consumption sum (kWh)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, vin: str, bike: dict, kind: str) -> None:
        super().__init__(coordinator, vin, bike, f"weekly_power_{kind}")
        self._kind = kind  # "charge" | "consume"
        self._attr_translation_key = (
            "weekly_charge" if kind == "charge" else "weekly_consume"
        )

    @property
    def native_value(self) -> float | None:
        records = self._extras.get("power_week") or []
        if not records:
            return None
        key = "powerCharge" if self._kind == "charge" else "powerConsume"
        total = sum(
            v for r in records if (v := _to_float(r.get(key))) is not None
        )
        return round(total, 2)

    @property
    def extra_state_attributes(self) -> dict:
        records = self._extras.get("power_week") or []
        return {
            "days": [
                {"date": r.get("recordDate")} for r in reversed(records)
            ],
        }


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
                YadeaIotExpireSensor(coordinator, vin, bike),
                YadeaVehicleInfoSensor(coordinator, vin, bike),
                YadeaMonthMileageSensor(coordinator, vin, bike),
                YadeaTotalMileageSensor(coordinator, vin, bike),
                YadeaTotalCO2Sensor(coordinator, vin, bike),
                YadeaDailyMileageSensor(coordinator, vin, bike),
                YadeaDailyPowerSensor(coordinator, vin, bike, "charge"),
                YadeaDailyPowerSensor(coordinator, vin, bike, "consume"),
                YadeaWeeklyPowerSensor(coordinator, vin, bike, "charge"),
                YadeaWeeklyPowerSensor(coordinator, vin, bike, "consume"),
            ]
        )
    async_add_entities(entities)
