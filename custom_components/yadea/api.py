"""Yadea cloud API client (async, aiohttp).

逆向成果封装（2026-11-06 实测验证）:
- 登录: POST /prod-api/user/loginByPassword {account, password, appType:1, accountType:1}
- 车辆列表(含实时 SOC/剩余里程快照): POST /prod-wg/api/app/userBike/queryBindingBike
- 网关签名: gatewaySign = base64(AES-128-ECB("gatewayTimestamp={ms}&json={payload}"))
"""
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.parse
from typing import Any

import aiohttp
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from .const import APP_ID, CODE_OK, SECRET, USER_BASE, USER_AGENT, WG_BASE

LOGGER = logging.getLogger(__package__)


class YadeaError(Exception):
    """Generic Yadea API error."""


class YadeaAuthError(YadeaError):
    """Auth/token error — caller should re-login."""


def _aes_encrypt(plain: str) -> str:
    cipher = AES.new(SECRET, AES.MODE_ECB)
    return base64.b64encode(cipher.encrypt(pad(plain.encode(), 16))).decode()


def _aes_decrypt(body: str) -> str:
    raw = base64.b64decode(urllib.parse.unquote(body.strip()))
    return unpad(AES.new(SECRET, AES.MODE_ECB).decrypt(raw), 16).decode()


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
        self.token_expire: int | None = None  # epoch seconds if provided

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
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        url = WG_BASE + path
        if method == "GET":
            return await self._request_json("GET", url, params=params, headers=headers)
        return await self._request_json("POST", url, data="", headers=headers)

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
        if "token" in msg.lower() or "登录" in msg:
            raise YadeaAuthError(f"[{code}] {msg}")
        raise YadeaError(f"[{code}] {msg}")

    # ---------- business endpoints ----------

    async def get_bikes(self) -> list[dict]:
        """Bound vehicles incl. live snapshot: soc1 (%), remMileage (km), updateTime..."""
        data = await self._wg_call("POST", "/userBike/queryBindingBike", data={})
        if isinstance(data, dict):
            data = data.get("records") or data.get("list") or []
        return data or []

    async def get_yesterday_power(self, vin: str) -> dict | None:
        """Latest daily power record (powerCharge / powerConsume, kWh)."""
        try:
            data = await self._wg_call(
                "POST",
                "/batt-power-record/statistics/page",
                data={"current": 1, "size": 1, "vin": vin},
            )
        except YadeaError as err:
            LOGGER.debug("power record failed for %s: %s", vin, err)
            return None
        records = (data or {}).get("records") or []
        return records[0] if records else None

    async def get_month_mileage(self, vin: str) -> float | None:
        """Current-month mileage (km), best effort."""
        try:
            data = await self._wg_call(
                "GET",
                "/vehicle-base/track-ride-meter/current-month/mileage",
                params={"vin": vin},
            )
        except YadeaError as err:
            LOGGER.debug("month mileage failed for %s: %s", vin, err)
            return None
        if isinstance(data, dict):
            for key in ("mileage", "totalMileage", "currentMonthMileage"):
                if key in data and data[key] is not None:
                    return float(data[key])
        return None

    async def get_bike_data(self) -> dict[str, Any]:
        """Full refresh: bikes + per-bike extras. Re-login once on auth error."""
        if not self.token:
            await self.login()
        try:
            return await self._collect()
        except YadeaAuthError:
            LOGGER.info("Yadea token invalid, re-login")
            await self.login()
            return await self._collect()

    async def _collect(self) -> dict[str, Any]:
        bikes = await self.get_bikes()
        result: dict[str, Any] = {"bikes": bikes, "extras": {}}
        for bike in bikes:
            vin = str(bike.get("vin", ""))
            extra: dict[str, Any] = {}
            power = await self.get_yesterday_power(vin)
            if power:
                extra["power"] = power
            mileage = await self.get_month_mileage(vin)
            if mileage is not None:
                extra["month_mileage"] = mileage
            result["extras"][vin] = extra
        return result
