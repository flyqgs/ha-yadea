"""Local ride-distance estimation for Yadea bikes.

雅迪云端对部分车型不生成骑行记录：所有 ``track-ride*`` 接口都返回空集，
``current-month/mileage`` 为 null、``total-mileage/calc/co2`` 恒为 0，
因此"本月里程 / 累计里程"无法从 API 读到真实值。

接口里唯一的距离信号是快照中的 ``remMileage``（车机估算的剩余续航，0.1 km）
与 ``soc1``（电量百分比）。本模块把两次轮询之间 remMileage 的**下降量**
累加到按日分桶里：

- 电量上升（充电中）时续航随之上升，不计入；
- 电量不变时只接受 2 km 以内的续航下降（捕捉不足 1% 的短途）；
- 单步超过 8 km 视为异常跳变，改用「电量差 × 每%可行驶里程」折算；
- 距上次成功采样超过 2 小时（HA 停机/断网）只重置基准、不补算。

数据写在 ``.storage/yadea_mileage_<entry_id>``，重启后累计值不丢。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

LOGGER = logging.getLogger(__package__)

STORAGE_VERSION = 1
MAX_STEP_KM = 8.0        # 两次轮询之间不可能骑超过 8 km
NO_SOC_STEP_KM = 2.0     # 电量未变时，只接受 2 km 以内的续航变化
MAX_GAP_SECONDS = 21600  # 超过 6 小时无新数据 -> 只重置基准
KEEP_DAYS = 400
SAVE_DELAY = 120


def _f(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value


class MileageTracker:
    """Integrate remMileage decreases into per-day distance buckets."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(
            hass, STORAGE_VERSION, f"yadea_mileage_{entry_id}"
        )
        self._data: dict[str, Any] = {"bikes": {}}
        self._loaded = False

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if isinstance(stored, dict) and isinstance(stored.get("bikes"), dict):
            self._data = {"bikes": stored["bikes"]}
        self._loaded = True

    # ------------------------------------------------------------------ update
    def update(self, bikes: list[dict], now: datetime) -> None:
        """Account one fresh snapshot (call after every coordinator poll)."""
        if not self._loaded:
            return

        stamp = now.strftime("%Y-%m-%d")
        ts = now.timestamp()
        changed = False

        for bike in bikes or []:
            vin = str(bike.get("vin") or "")
            soc = _f(bike.get("soc1"))
            rem = _f(bike.get("remMileage"))
            if not vin or soc is None or rem is None:
                continue

            rec = self._data["bikes"].setdefault(vin, {"days": {}, "total": 0.0})
            days: dict[str, float] = rec.setdefault("days", {})
            last = rec.get("last")

            if isinstance(last, dict):
                step = self._step(last, soc, rem, ts)
                if step > 0:
                    days[stamp] = round(days.get(stamp, 0.0) + step, 2)
                    rec["total"] = round(float(rec.get("total") or 0.0) + step, 2)
                    changed = True
                elif step < 0:
                    # 续航回升（温度/估算修正）：抵扣当天已记的里程，避免噪声单向累积
                    if days.get(stamp):
                        days[stamp] = max(0.0, round(days[stamp] + step, 2))
                        changed = True

            rec["last"] = {"ts": ts, "soc": soc, "rem": rem}

        if changed:
            self._trim(now)
            self._store.async_delay_save(lambda: self._data, SAVE_DELAY)

    @staticmethod
    def _step(last: dict, soc: float, rem: float, ts: float) -> float:
        """Return the distance ridden between two samples (0 when unknown)."""
        last_soc = _f(last.get("soc"))
        last_rem = _f(last.get("rem"))
        last_ts = _f(last.get("ts"))
        if last_soc is None or last_rem is None or last_ts is None:
            return 0.0

        gap = ts - last_ts
        if gap <= 0 or gap > MAX_GAP_SECONDS:
            return 0.0

        d_soc = last_soc - soc   # >0: 电量下降（行驶）
        d_rem = last_rem - rem   # >0: 续航下降（行驶）

        if d_soc < 0:            # 充电中，续航回升不计
            return 0.0

        if d_soc == 0:
            # 电量没动：只认小幅续航变化（短途骑行）；回升取负值用于抵扣噪声
            if -NO_SOC_STEP_KM <= d_rem <= NO_SOC_STEP_KM and d_rem != 0:
                return d_rem
            return 0.0

        if 0 < d_rem <= MAX_STEP_KM:
            return d_rem

        # 续航没跟上（或跳变）：用当前「每 1% 电量可行驶里程」折算
        km_per_pct = rem / soc if soc > 0 else 0.0
        est = d_soc * km_per_pct
        return est if 0 < est <= MAX_STEP_KM else 0.0

    def _trim(self, now: datetime) -> None:
        cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        for rec in self._data["bikes"].values():
            days = rec.get("days") or {}
            for day in [d for d in days if d < cutoff]:
                days.pop(day, None)

    # ------------------------------------------------------------------- read
    def stats(self, vin: str, now: datetime) -> dict[str, Any]:
        """Aggregate the buckets for one vehicle."""
        rec = self._data["bikes"].get(vin) or {}
        days: dict[str, float] = rec.get("days") or {}
        today = now.strftime("%Y-%m-%d")
        week_from = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        month_from = now.strftime("%Y-%m-01")

        last = rec.get("last") or {}
        return {
            "has_samples": bool(last),
            "today": round(days.get(today, 0.0), 2),
            "week": round(
                sum(v for d, v in days.items() if d >= week_from), 2
            ),
            "month": round(
                sum(v for d, v in days.items() if d >= month_from), 2
            ),
            "total": round(float(rec.get("total") or 0.0), 2),
            "last_soc": last.get("soc"),
            "last_range": last.get("rem"),
            "sample_days": len(days),
            "last_7_days": [
                {"date": d, "km": round(days[d], 2)}
                for d in sorted(days)[-7:]
            ],
        }

    @property
    def day_count(self) -> int:
        return sum(
            len((rec.get("days") or {})) for rec in self._data["bikes"].values()
        )
