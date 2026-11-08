"""Sensors for the Yadea integration.

每台车一个设备。数据来源两类：
- 车辆列表快照：soc1 电量、remMileage 剩余里程、updateTime 上报时间、物联卡
- 云端轨迹/里程接口：今日/本周/本月/累计里程、骑行时长与次数、最近一次骑行
  （轨迹按账号归属，共享账号查不到任何记录）
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
    PERCENTAGE,
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
        # 全局唯一 ID：条目ID + VIN + 实体类型。VIN 跨账号共享（家人共享）时
        # 两个集成条目各自拥有独立实体，互不冲突；同一车辆仍归入同一设备。
        self._attr_unique_id = (
            f"{coordinator.entry.entry_id}_{vin}_{entity_key}"
        )
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
    def _track(self) -> dict:
        return self._extras.get("track") or {}

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
    _attr_native_unit_of_measurement = PERCENTAGE
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


# ---------------------------------------------------------------- 里程 / 轨迹


class YadeaMileageSensor(YadeaEntity):
    """云端里程统计（今日 / 本周 / 本月 / 累计）。"""

    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_icon = "mdi:map-marker-distance"

    # period -> (unique_id 后缀, translation_key, state_class)
    # month/total 沿用 1.x 的 entity_key，保证升级后实体 ID 不变
    _PERIODS = {
        "today": ("today_mileage", "today_mileage", SensorStateClass.MEASUREMENT),
        "week": ("week_mileage", "week_mileage", SensorStateClass.MEASUREMENT),
        "month": ("month_mileage", "month_mileage", SensorStateClass.MEASUREMENT),
        "total": ("total_mileage", "total_mileage", SensorStateClass.TOTAL_INCREASING),
    }

    def __init__(
        self, coordinator: YadeaCoordinator, vin: str, bike: dict, period: str
    ) -> None:
        key, translation_key, state_class = self._PERIODS[period]
        super().__init__(coordinator, vin, bike, key)
        self._period = period
        self._attr_translation_key = translation_key
        self._attr_state_class = state_class

    @property
    def native_value(self) -> float | None:
        track = self._track
        if not track:
            return None
        if self._period == "total":
            # 没有账号累计里程时不要显示 0
            value = (track.get("total") or {}).get("km")
            return value or None
        if not track.get("has_records") and self._period in ("today", "week"):
            return 0.0
        return (track.get(self._period) or {}).get("km")

    @property
    def extra_state_attributes(self) -> dict:
        track = self._track
        attrs: dict[str, Any] = {"source": "雅迪云端轨迹统计"}
        if self._period == "today":
            today = track.get("today") or {}
            attrs["rides"] = today.get("rides")
            attrs["minutes"] = today.get("minutes")
            attrs["max_speed"] = today.get("max_speed")
            attrs["segments"] = today.get("segments") or []
        elif self._period == "week":
            monday = dt_util.now(CN_TZ).date() - timedelta(
                days=dt_util.now(CN_TZ).date().weekday()
            )
            attrs["week_start"] = monday.isoformat()
            attrs["days"] = [
                d for d in (track.get("days") or []) if d.get("date", "") >= monday.isoformat()
            ]
        elif self._period == "month":
            attrs["rides"] = (track.get("month") or {}).get("rides")
            attrs["days"] = (track.get("days") or [])[:31]
        else:
            total = track.get("total") or {}
            attrs["co2_kg"] = total.get("co2")
            attrs["kg_per_km"] = total.get("kg_per_km")
        return attrs


class YadeaTrackCountSensor(YadeaEntity):
    """今日骑行次数。"""

    _attr_icon = "mdi:bike-fast"
    _attr_translation_key = "today_rides"

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "today_rides")

    @property
    def native_value(self) -> int | None:
        today = self._track.get("today")
        return today.get("rides") if today is not None else None


class YadeaTodayRideTimeSensor(YadeaEntity):
    """今日骑行时长（分钟）。"""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_icon = "mdi:timer-outline"
    _attr_translation_key = "today_ride_time"

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "today_ride_time")

    @property
    def native_value(self) -> int | None:
        today = self._track.get("today")
        return today.get("minutes") if today is not None else None


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
        value = (self._track.get("total") or {}).get("co2")
        return value or None

    @property
    def extra_state_attributes(self) -> dict:
        total = self._track.get("total") or {}
        return {
            "kg_per_km": total.get("kg_per_km"),
            "account_mileage": total.get("km"),
        }


class YadeaLastRideSensor(YadeaEntity):
    """最近一次骑行的开始时间。"""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:history"
    _attr_translation_key = "last_ride_time"

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "last_ride_time")

    @property
    def native_value(self) -> datetime | None:
        dt = _parse_cn_time((self._track.get("last_ride") or {}).get("start"))
        return dt_util.as_utc(dt) if dt else None

    @property
    def extra_state_attributes(self) -> dict:
        ride = self._track.get("last_ride") or {}
        return {
            "end": ride.get("end"),
            "km": ride.get("km"),
            "minutes": ride.get("minutes"),
            "avg_speed": ride.get("avg_speed"),
            "max_speed": ride.get("max_speed"),
            "consume_power": ride.get("power"),
            "route_from": ride.get("route_from"),
            "route_to": ride.get("route_to"),
            "gps_points": ride.get("gps_points"),
        }


class YadeaLastRideDetailSensor(YadeaEntity):
    """最近一次骑行的里程 / 时长 / 极速 / 路线。"""

    _attr_icon = "mdi:bike"

    _FIELDS = {
        "mileage": ("last_ride_distance", "mdi:map-marker-path", SensorDeviceClass.DISTANCE,
                    UnitOfLength.KILOMETERS, SensorStateClass.MEASUREMENT),
        "duration": ("last_ride_duration", "mdi:timer-sand", SensorDeviceClass.DURATION,
                     UnitOfTime.MINUTES, SensorStateClass.MEASUREMENT),
        "max_speed": ("last_ride_max_speed", "mdi:speedometer", None,
                      UnitOfLength.KILOMETERS + "/h", SensorStateClass.MEASUREMENT),
    }

    def __init__(
        self, coordinator: YadeaCoordinator, vin: str, bike: dict, field: str
    ) -> None:
        key, icon, device_class, unit, state_class = self._FIELDS[field]
        super().__init__(coordinator, vin, bike, key)
        self._field = field
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class

    @property
    def native_value(self) -> float | None:
        ride = self._track.get("last_ride") or {}
        if self._field == "mileage":
            return ride.get("km")
        if self._field == "duration":
            return ride.get("minutes")
        return ride.get("max_speed")


class YadeaLastRideRouteSensor(YadeaEntity):
    """最近一次骑行的起终点地址。"""

    _attr_icon = "mdi:map-marker-radius"
    _attr_translation_key = "last_ride_route"

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        super().__init__(coordinator, vin, bike, "last_ride_route")

    @property
    def native_value(self) -> str | None:
        ride = self._track.get("last_ride") or {}
        start, end = ride.get("route_from"), ride.get("route_to")
        if not start and not end:
            return None
        return f"{start or '未知'} → {end or '未知'}"

    @property
    def extra_state_attributes(self) -> dict:
        ride = self._track.get("last_ride") or {}
        return {
            "route_from": ride.get("route_from"),
            "route_to": ride.get("route_to"),
            "start": ride.get("start"),
            "end": ride.get("end"),
        }


class YadeaDailyTracksSensor(YadeaEntity):
    """近 30 天每日骑行汇总（诊断用，明细放属性）。"""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_icon = "mdi:calendar-clock"
    _attr_translation_key = "daily_tracks"

    def __init__(self, coordinator: YadeaCoordinator, vin: str, bike: dict) -> None:
        # 沿用 1.x 的 daily_mileage key，避免升级后实体 ID 漂移
        super().__init__(coordinator, vin, bike, "daily_mileage")

    @property
    def native_value(self) -> float | None:
        days = self._track.get("days") or []
        if not days:
            return None
        return round(sum(d.get("km") or 0 for d in days[:7]), 2)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "days": self._track.get("days") or [],
            "last_7_days_km": self.native_value,
            "source": "雅迪云端轨迹统计",
        }


class YadeaDailyPowerSensor(YadeaEntity):
    """Latest daily power record (charge / consume)."""

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


class YadeaWeeklyPowerSensor(YadeaEntity):
    """Rolling 7-day charge / consumption sum."""

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
                # 云端轨迹/里程
                YadeaMileageSensor(coordinator, vin, bike, "today"),
                YadeaMileageSensor(coordinator, vin, bike, "week"),
                YadeaMileageSensor(coordinator, vin, bike, "month"),
                YadeaMileageSensor(coordinator, vin, bike, "total"),
                YadeaTrackCountSensor(coordinator, vin, bike),
                YadeaTodayRideTimeSensor(coordinator, vin, bike),
                YadeaTotalCO2Sensor(coordinator, vin, bike),
                YadeaLastRideSensor(coordinator, vin, bike),
                YadeaLastRideDetailSensor(coordinator, vin, bike, "mileage"),
                YadeaLastRideDetailSensor(coordinator, vin, bike, "duration"),
                YadeaLastRideDetailSensor(coordinator, vin, bike, "max_speed"),
                YadeaLastRideRouteSensor(coordinator, vin, bike),
                YadeaDailyTracksSensor(coordinator, vin, bike),
                # 用电
                YadeaDailyPowerSensor(coordinator, vin, bike, "charge"),
                YadeaDailyPowerSensor(coordinator, vin, bike, "consume"),
                YadeaWeeklyPowerSensor(coordinator, vin, bike, "charge"),
                YadeaWeeklyPowerSensor(coordinator, vin, bike, "consume"),
            ]
        )
    async_add_entities(entities)
