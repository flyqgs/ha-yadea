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
