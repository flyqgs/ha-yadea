# 逆向技术笔记：雅迪智行云端 API

> 本文记录逆向过程中确认的接口与签名机制（2026-11-06 实测验证），
> 供社区研究使用。仅供接入自己名下的车辆。

## 端点

| 用途 | 地址 |
|---|---|
| 登录（纯 JSON，无需签名） | `POST https://tspapph5.yadeaiot.com.cn/prod-api/user/loginByPassword` |
| 发送短信验证码 | `POST https://tspapph5.yadeaiot.com.cn/prod-api/user/sendCode` |
| 车辆数据（网关签名式） | `https://tspapph5.yadeaiot.com.cn/prod-wg/api/app/...` |
| 主网关 | `https://gw.yadeaiot.com.cn/api/app/...` |

## 密码登录

请求体（字段名经服务端实测确认）：

```json
{
  "account": "手机号或邮箱",
  "password": "明文密码",
  "appType": 1,
  "accountType": 1
}
```

响应 `data` 中含 `token`（JWT，长期有效）与用户信息。
注意：登录 DTO 里**没有** `mobile`/`passWord`/`appname` 字段——
"appname不能为空" 之类的报错文本有误导性，真实字段是 `appType`（Integer）。

## 网关签名（H5 前端 webpack 模块 7e14）

车辆数据接口需要三个请求头：

- `gatewayAppId`: H5 端为 `cfb00038ddac4c9ebc85821694bbd3fa`
- `gatewayTimestamp`: 毫秒时间戳
- `gatewaySign`: `base64(AES-128-ECB(key, "gatewayTimestamp={ts}&json={payload}"))`
  - key = `RFC1J02e116b45a0`（16 字节，PKCS7 填充）
  - payload 为 JSON 序列化后的请求体，紧凑格式（无空格）
- 另加 `Authorization: Bearer {token}`

**网关解密 gatewaySign 头来取业务参数**——即真实 HTTP body 可以为空，
参数全部在签名里传递。

## 关键数据接口

| 接口 | 方法 | 参数 |
|---|---|---|
| `/userBike/queryBindingBike` | POST | `{}`（返回含实时快照！） |
| `/battSummary/queryBattInfo` | GET | `{vin, type}` |
| `/bikeRealStatus/getVehRealStatus` | GET | `{vin}` |
| `/bikeBatteryAndMileage/queryBikeBatteryAndMileage` | POST | `{current, size, vin}` |
| `/battSummary/queryCoordinateDataPage` | POST | `{current, size, socType, vin}` |
| `/vehicle-base/track-ride-meter/summary` | GET | `{vin, metricType, type, startDate, endDate, smartType}` |
| `/vehicle-base/track-ride-meter/current-month/mileage` | GET | `{vin}` |
| `/batt-power-record/statistics/page` | POST | `{current, size, vin}` |

### 里程数据实测结论（2026-11-08 修正）

> **重要修正（2026-11-08 二次实测）**：此前所有 `track-ride*` 接口报
> 「车架号不存在 / 数据不存在」**不是云端没数据，而是我们的调用方式错了**。
> H5 请求拦截器（chunk-common 模块 6500）在签名后执行
> `e.params = undefined; e.data = undefined` —— **参数只放在 gatewaySign 里，
> URL 上不能带 query，POST 也不能带 body**。
> 同时带 query + 签名时，网关读到错组合，所有 `/vehicle-base/*` 接口都会失败。
> 改为纯签名调用后，车主账号下轨迹数据完整返回（见下文「骑行轨迹接口」）。
> 另：轨迹/里程按**账号**归属，家人共享账号（`primaryUser=false`）查询一律为空。

#### 骑行轨迹接口（车主账号可用）

| 接口 | 方法 | 参数 | 说明 |
|---|---|---|---|
| `/vehicle-base/track-ride-detail/page-list` | GET | `{vin, metricType:0, current, size, type:1}` | 按天分组的骑行轨迹，`records[].trackRideQueryVOList` 为分段（起止时间/里程/均速/极速/耗电/CO2）；每页约 7 天，翻页边界会重复返回同一天，需按日期去重 |
| `/vehicle-base/track-ride/summary` | GET | `{vin, metricType:0, type:1日/2周/3月, startDate, endDate, smartType:2}` | 日/周/月聚合：`rideCount/totalMileage/totalTrackTime/averageSpeed/maxSpeed/totalPower` |
| `/vehicle-base/track-ride/calc/co2` | GET | `{vin, metricType:0}` | `{accountMileage, totalCO2, kgPerKm}` —— App 首页的「累计总里程 / 减碳」 |
| `/vehicle-base/track-ride-detail/lon-lat` | GET | `{id, type:1}` | 某段轨迹的 GPS 点（经纬度 + 部分点带 `beginAddress/endAddress` 地址） |
| `/vehicle-base/track-ride-meter-detail/page-list` | GET | `{vin, metricType, current, size, type:2}` | 仪表盘轨迹（部分车型恒为 0） |

### 历史结论存档（调用方式错误时 observed，仅作参考）

| 接口 | 当时实测结果 |
|---|---|
| `track-ride-meter/summary`（type=1/2/3） | `[]`（查询成功但无记录） |
| `track-ride-meter/current-month/mileage` | `data: null`（接口已废弃） |
| `track-ride-meter/total-mileage/calc/co2` | `{"totalCO2":0.0,"kgPerKm":0.18,"vehicleTotalMileage":0}`（仪表口径，多数车型无数据） |
| `track-ride/summary` | `200000 车架号不存在`（实为签名/参数双发所致） |
| `vehiclebase/estimateMileage/cal2`（POST `{vin,soc1}`） | `{"formula":2,"estimateMileage":<remMileage>}`，即剩余续航 |

**结论（v2.0.0）**：里程/轨迹使用云端骑行记录（车主账号），不再做本地估算。
共享账号下查询恒为空属正常现象（数据按账号归属）。

成功响应 `code: "000000"`，业务数据在 `data` 中（部分接口返回 AES 加密体，
用同一密钥解密后为 `gatewayTimestamp=...&json=...` 格式）。

**重要**：`queryBindingBike` 返回的车辆对象自带实时快照字段
（`soc1` 电量、`remMileage` 剩余里程、`updateTime`、`batteryCount`、
`iotExpireDays` 等），轮询它一次即可覆盖所有车辆。
而 `queryCoordinateDataPage` 的 `xaxis/yaxis` 是 **SOC 曲线图的图表坐标**，
不是 GPS 经纬度。

## 逆向过程要点（供参考）

- 雅迪智行 Android App（com.yadea.smartmoto）为 360 加固 + Flutter 混合架构，
  Java 层不可静态反编译；但其内嵌的 **H5 前端 JS 未加密**，
  全部网关配置、API 路径、签名算法都能从 H5 bundle 中提取。
- 原生 App 专属的 appId（`0e7b1d094c7b4073b7c958edbbbc1115`）使用另一把密钥
  （在加固 dex 中），H5 的 appId 即可满足只读需求。
- 字段名探测技巧：给候选字段赋值 `[1]`，若 DTO 中存在该字段，
  Jackson 会报 `720500 JSON parse error`——与业务校验无关、100% 可靠的
  字段存在性信号。
