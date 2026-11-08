"""Yadea cloud API client (async, aiohttp).

逆向成果（2026-11-08 二次实测修正）:

1. 登录
   POST /prod-api/user/loginByPassword {account, password, appType:1, accountType:1}
   返回 token + tokenExpire（约 1 年）。**雅迪单会话**：任何新登录都会让旧 token
   立即失效（100995），所以 HA 与手机 App 无法同时在线。

2. 网关签名（关键修正）
   H5 请求拦截器（chunk-common 模块 6500）的真实行为::

       gatewaySign = AES128ECB("gatewayTimestamp={ms}&json={payload}")
       e.params = undefined;  e.data = undefined;

   即参数**只**放在 gatewaySign 的明文 JSON 里，URL 上不带 query、body 也不带。
   之前的实现同时发 query + 签名，网关会读到错组合，所有 /vehicle-base/* 接口
   都返回 200000「车架号不存在」或 099992「车架号不能为空」。

3. 轨迹/里程（需车主账号，共享账号查不到任何记录）
   GET  /vehicle-base/track-ride-detail/page-list   {vin, metricType, current, size, type}
        type=1 骑行轨迹（按天分组，记录里 trackRideQueryVOList 是分段）
   GET  /vehicle-base/track-ride/summary            {vin, metricType, type(1日/2周/3月),
                                                     startDate, endDate, smartType}
   GET  /vehicle-base/track-ride/calc/co2           {vin, metricType}
        -> {accountMileage, totalCO2, kgPerKm} 累计里程/减碳
   GET  /vehicle-base/track-ride-detail/lon-lat     {id, type=1}
        -> GPS 点，部分点带 beginAddress / endAddress
"""
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any

import aiohttp
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from .const import APP_ID, CODE_OK, SECRET, USER_BASE, USER_AGENT, WG_BASE

LOGGER = logging.getLogger(__package__)

CN_TZ = timezone(timedelta(hours=8))


class YadeaError(Exception):
    """Generic Yadea API error."""


class YadeaAuthError(YadeaError):
    """Auth/token error — caller should ask the user to re-authorize."""


def _aes_encrypt(plain: str) -> str:
    cipher = AES.new(SECRET, AES.MODE_ECB)
    return base64.b64encode(cipher.encrypt(pad(plain.encode(), 16))).decode()


def _aes_decrypt(body: str) -> str:
    raw = base64.b64decode(urllib.parse.unquote(body.strip()))
    return unpad(AES.new(SECRET, AES.MODE_ECB).decrypt(raw), 16).decode()


def _f(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class YadeaApi:
    """Async client for the Yadea cloud."""

    def __init__(
        self,
        account: str,
        password: str,
        session: aiohttp.ClientSession,
    ) -> None:
        self.account = account
        self.password = password
        self._session = session
        self.token: str | None = None
        self.token_expire: int | None = None
        # Optional async callback fired after every successful login,
        # used to persist the token so HA restarts don't re-login
        # (re-login kicks the mobile app session — single active session).
        self.token_callback: Any = None

    # ---------- low level ----------

    async def login(self) -> dict[str, Any]:
        """Password login. Verified field layout (2026-11-06)."""
        payload = {
            "account": self.account,
            "password": self.password,
            "appType": 1,
            "accountType": 1,
        }
        data = await self._user_post("/loginByPassword", payload)
        self.token = data.get("token")
        self.token_expire = data.get("tokenExpire")
        if not self.token:
            raise YadeaAuthError("登录成功但响应中没有 token")
        if self.token_callback is not None:
            await self.token_callback(self.token)
        return data

    async def _user_post(self, path: str, payload: dict) -> dict:
        return await self._request_json(
            "POST",
            USER_BASE + path,
            json_body=payload,
            headers={"Content-Type": "application/json"},
        )

    async def _wg_call(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        data: dict | None = None,
    ) -> dict:
        """Call the signed gateway.

        参数只进签名，不带 query / body —— 这是 H5 的真实行为，带 query 会让
        网关解析出错误的参数组合（见模块 docstring）。
        """
        ts = int(time.time() * 1000)
        payload = data if data is not None else (params or {})
        sign_plain = (
            f"gatewayTimestamp={ts}&json="
            f"{json.dumps(payload, separators=(',', ':'))}"
        )
        headers = {
            "gatewayAppId": APP_ID,
            "gatewayTimestamp": str(ts),
            "gatewaySign": _aes_encrypt(sign_plain),
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return await self._request_json(method, WG_BASE + path, headers=headers)

    async def _request_json(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        data: Any = None,
        json_body: dict | None = None,
        headers: dict | None = None,
    ) -> dict:
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                data=data,
                json=json_body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                text = await resp.text()
        except aiohttp.ClientError as err:
            raise YadeaError(f"网络错误: {err}") from err

        try:
            body = json.loads(_aes_decrypt(text))
        except Exception:
            try:
                body = json.loads(text)
            except Exception as err:
                raise YadeaError(f"无法解析响应 (HTTP {resp.status}): {text[:120]}") from err

        code = str(body.get("code", ""))
        if code == CODE_OK:
            return body.get("data") if "data" in body else body

        msg = str(body.get("msg", body))
        if code == "100995" or "token" in msg.lower() or "登录" in msg:
            raise YadeaAuthError(f"[{code}] {msg}")
        raise YadeaError(f"[{code}] {msg}")

    # ---------- 车辆基础 ----------

    async def get_bikes(self) -> list[dict]:
        """Bound vehicles incl. live snapshot: soc1 (%), remMileage (km), updateTime..."""
        data = await self._wg_call("POST", "/userBike/queryBindingBike", data={})
        if isinstance(data, dict):
            data = data.get("records") or data.get("list") or []
        return data or []

    async def get_power_history(self, vin: str, size: int = 7) -> list[dict]:
        """Latest N daily power records, newest first (powerCharge/powerConsume)."""
        try:
            data = await self._wg_call(
                "POST",
                "/batt-power-record/statistics/page",
                data={"current": 1, "size": size, "vin": vin},
            )
        except YadeaAuthError:
            raise
        except YadeaError as err:
            LOGGER.debug("power history failed for %s: %s", vin, err)
            return []
        return (data or {}).get("records") or []

    # ---------- 轨迹 / 里程 ----------

    async def get_track_days(self, vin: str, pages: int = 1, size: int = 30) -> list[dict]:
        """按天分组的骑行轨迹，最新在前。

        服务端每页约 7 天，且翻页边界会重复返回同一天 —— 按 createDate 去重，
        保留分段最多的那条。
        """
        days: dict[str, dict] = {}
        for current in range(1, pages + 1):
            try:
                data = await self._wg_call(
                    "GET",
                    "/vehicle-base/track-ride-detail/page-list",
                    params={"vin": vin, "metricType": 0, "current": current,
                            "size": size, "type": 1},
                )
            except YadeaAuthError:
                raise
            except YadeaError as err:
                LOGGER.debug("track page %s failed for %s: %s", current, vin, err)
                break
            records = (data or {}).get("records") or []
            for rec in records:
                key = str(rec.get("createDate") or "")
                if not key:
                    continue
                old = days.get(key)
                if old is None or len(rec.get("trackRideQueryVOList") or []) > len(
                    old.get("trackRideQueryVOList") or []
                ):
                    days[key] = rec
            if not (data or {}).get("hasNext"):
                break
        return [days[k] for k in sorted(days, reverse=True)]

    async def get_track_summary(
        self, vin: str, rtype: int, start: date, end: date, smart_type: int = 2
    ) -> list[dict]:
        """里程统计聚合。rtype: 1=日 2=周 3=月。"""
        try:
            data = await self._wg_call(
                "GET",
                "/vehicle-base/track-ride/summary",
                params={"vin": vin, "metricType": 0, "type": rtype,
                        "startDate": start.strftime("%Y-%m-%d"),
                        "endDate": end.strftime("%Y-%m-%d"),
                        "smartType": smart_type},
            )
        except YadeaAuthError:
            raise
        except YadeaError as err:
            LOGGER.debug("track summary type=%s failed for %s: %s", rtype, vin, err)
            return []
        return data if isinstance(data, list) else []

    async def get_account_mileage(self, vin: str) -> dict:
        """累计里程与减碳：{accountMileage, totalCO2, kgPerKm}。"""
        try:
            data = await self._wg_call(
                "GET",
                "/vehicle-base/track-ride/calc/co2",
                params={"vin": vin, "metricType": 0},
            )
        except YadeaAuthError:
            raise
        except YadeaError as err:
            LOGGER.debug("account mileage failed for %s: %s", vin, err)
            return {}
        return data or {}

    async def get_track_gps(self, track_id: Any) -> list[dict]:
        """某段轨迹的 GPS 点（含起终点地址）。"""
        try:
            data = await self._wg_call(
                "GET",
                "/vehicle-base/track-ride-detail/lon-lat",
                params={"id": track_id, "type": 1},
            )
        except YadeaAuthError:
            raise
        except YadeaError as err:
            LOGGER.debug("track gps failed for %s: %s", track_id, err)
            return []
        return data if isinstance(data, list) else []

    async def get_track_data(self, vin: str, smart_type: int = 2) -> dict[str, Any]:
        """聚合一车的轨迹/里程数据，供传感器使用。"""
        today = datetime.now(CN_TZ).date()
        month_start = today.replace(day=1)

        days = await self.get_track_days(vin, pages=1)
        summary = await self.get_track_summary(vin, 1, today - timedelta(days=40), today, smart_type)
        total = await self.get_account_mileage(vin)

        # 日汇总以 summary 为准（覆盖完整一个月，页面只有最近 7 天）
        per_day: dict[str, dict] = {}
        for row in summary:
            key = str(row.get("startDate") or "")
            if not key:
                continue
            per_day[key] = {
                "date": key,
                "km": round(_f(row.get("totalMileage")) or 0.0, 2),
                "minutes": round((_f(row.get("totalTrackTime")) or 0) / 60),
                "rides": int(_f(row.get("rideCount")) or 0),
                "max_speed": _f(row.get("maxSpeed")),
            }
        # 页面里今天有记录但 summary 还没刷新时补上
        for rec in days:
            key = str(rec.get("createDate") or "")
            if key and key not in per_day:
                per_day[key] = {
                    "date": key,
                    "km": round(_f(rec.get("totalMileage")) or 0.0, 2),
                    "minutes": round((_f(rec.get("totalTrackTime")) or 0) / 60),
                    "rides": len(rec.get("trackRideQueryVOList") or []),
                    "max_speed": None,
                }

        def _sum(start: date, end: date) -> float:
            return round(
                sum(v["km"] for k, v in per_day.items()
                    if start <= date.fromisoformat(k) <= end),
                2,
            )

        monday = today - timedelta(days=today.weekday())
        today_row = per_day.get(today.isoformat(), {})

        # 最近一段骑行（分段里出发时间最晚的一条）
        segments: list[dict] = []
        for rec in days:
            for seg in rec.get("trackRideQueryVOList") or []:
                segments.append(seg)
        segments = [s for s in segments if (_f(s.get("mileage")) or 0) > 0]
        segments.sort(key=lambda s: str(s.get("startTime") or ""), reverse=True)

        last_ride: dict[str, Any] = {}
        if segments:
            seg = segments[0]
            last_ride = {
                "id": seg.get("id"),
                "start": seg.get("startTime"),
                "end": seg.get("endTime"),
                "km": round(_f(seg.get("mileage")) or 0.0, 2),
                "minutes": round((_f(seg.get("trackTime")) or 0) / 60),
                "avg_speed": _f(seg.get("averageSpeed")),
                "max_speed": _f(seg.get("maxSpeed")),
                "power": _f(seg.get("consumePower")),
                "co2": _f(seg.get("co2Reduce")),
            }
            gps = await self.get_track_gps(seg.get("id"))
            if gps:
                start_addr = next((p.get("beginAddress") for p in gps if p.get("beginAddress")), None)
                end_addr = next(
                    (p.get("endAddress") for p in reversed(gps) if p.get("endAddress")), None
                )
                last_ride["route_from"] = start_addr
                last_ride["route_to"] = end_addr
                last_ride["gps_points"] = len(gps)

        return {
            "today": {
                "km": today_row.get("km", 0.0),
                "minutes": today_row.get("minutes", 0),
                "rides": today_row.get("rides", 0),
                "max_speed": today_row.get("max_speed"),
                "segments": [
                    {
                        "start": s.get("startTime"),
                        "end": s.get("endTime"),
                        "km": round(_f(s.get("mileage")) or 0.0, 2),
                        "minutes": round((_f(s.get("trackTime")) or 0) / 60),
                        "max_speed": _f(s.get("maxSpeed")),
                    }
                    for s in segments
                    if str(s.get("startTime") or "").startswith(today.isoformat())
                ],
            },
            "week": {"km": _sum(monday, today)},
            "month": {
                "km": _sum(month_start, today),
                "rides": sum(v["rides"] for k, v in per_day.items()
                             if k.startswith(month_start.isoformat()[:8])),
            },
            "total": {
                "km": round(_f(total.get("accountMileage")) or 0.0, 1),
                "co2": round(_f(total.get("totalCO2")) or 0.0, 1),
                "kg_per_km": _f(total.get("kgPerKm")),
            },
            "last_ride": last_ride,
            "days": [per_day[k] for k in sorted(per_day, reverse=True)][:31],
            "has_records": bool(per_day),
        }

    # ---------- 汇总 ----------

    async def get_bike_data(self) -> dict[str, Any]:
        """Full refresh: bikes + per-bike extras.

        不做自动重登：雅迪单会话，重登会把手机 App 顶下线。token 失效时抛
        YadeaAuthError，由上层触发重新授权（用户手动输入密码）。
        """
        if not self.token:
            raise YadeaAuthError("没有可用 token，请重新授权")
        return await self._collect()

    async def _collect(self, with_tracks: bool = True) -> dict[str, Any]:
        bikes = await self.get_bikes()
        result: dict[str, Any] = {"bikes": bikes, "extras": {}}
        for bike in bikes:
            vin = str(bike.get("vin", ""))
            extra: dict[str, Any] = {}
            history = await self.get_power_history(vin)
            if history:
                extra["power"] = history[0]  # newest day
                extra["power_week"] = history
            try:
                smart_type = int(bike.get("bikeInfoSmartType") or 2)
            except (TypeError, ValueError):
                smart_type = 2
            if with_tracks:
                extra["track"] = await self.get_track_data(vin, smart_type=smart_type)
            result["extras"][vin] = extra
        return result

    async def get_status_data(self) -> dict[str, Any]:
        """只取车辆状态（不含轨迹），用于高频轮询。"""
        return await self._collect(with_tracks=False)
