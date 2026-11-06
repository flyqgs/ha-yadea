# Yadea (雅迪智行) for Home Assistant

非官方的 Home Assistant 自定义集成，通过雅迪智行云端 API 读取绑定车辆的实时状态。

> **English**: An unofficial Home Assistant custom integration for Yadea electric
> scooters, reading real-time vehicle status from the Yadea Smart (雅迪智行) cloud.

## 功能特性

- ✅ 账号密码登录（雅迪智行 App 同款账号）
- ✅ 多车辆支持：账号下所有绑定车辆各建一个设备
- ✅ Token 过期自动重新登录
- ✅ 轮询间隔可调（30~600 秒，默认 60 秒）
- ✅ 中英文界面

### 每台车提供的传感器

| 实体 | 说明 | 来源 |
|---|---|---|
| 电量 (battery) | 电池 SOC（%） | 车辆快照 `soc1` |
| 剩余里程 (range) | km | 车辆快照 `remMileage` |
| 最后上报时间 (last_report) | 云端快照时间 | 快照 `updateTime` |
| 电池数量 (battery_count) | 电池组数 | 快照 `batteryCount` |
| 物联卡剩余天数 (iot_days) | 4G 卡到期天数 | 快照 `iotExpireDays` |
| 物联卡到期日 (iot_expire) | 到期日期 | 快照 `iotCardExpireTime` |
| 车辆信息 (vehicle_info) | 诊断实体：型号/颜色/MAC/蓝牙名/ICCID/绑定日期/保修等属性 | 车辆快照 |
| 本月里程 (month_mileage) | km | 里程统计接口 |
| 累计里程 (total_mileage) | km | `total-mileage/calc/co2` |
| 累计碳减排 (total_co2) | kg（附 kgPerKm 属性） | `total-mileage/calc/co2` |
| 近7天每日里程 (daily_mileage) | 诊断实体，7 天合计 km + 逐日列表属性 | `track-ride-meter/summary type=1`（仅 App 记录过骑行才有数据） |

此外每台车还有一个 **图片实体 (image)**：车辆官方车型图（`imgUrl`），
可直接用于仪表盘的图片卡片。

### 仪表盘美化示例

仓库里的 [`dashboard-example.yaml`](dashboard-example.yaml) 含 5 个视图：
**总览·经典**（纯核心卡片）、**精简总览**、**状态速览**（图文融合）、
**进阶版**（mushroom + mini-graph）与 **旗舰版**（深色 HUD 控制台，
含双车 hero 卡、7 天骑行条形图、双车对比镜像条、能耗网格与车辆档案，
需 [card-mod](https://github.com/thomasloven/lovelace-card-mod)）。
复制到自定义仪表盘并替换实体 ID 即可。

设备页会显示车辆官方图片（`imgUrl`）。

### ⚠️ 关于账号互踢（重要）

雅迪云端**同一账号只允许一个活跃会话**：HA 重新登录会使手机 App 掉线，反之亦然。
本集成的缓解措施：token 持久化存储，HA 重启/重载**不会**触发重新登录，
仅在 token 真正失效时才重登一次。但如果你经常在 App 上手动退出/切换登录，
仍可能互相影响。

**推荐做法**：注册一个新的雅迪账号（小号），在 App 里通过
**"家人共享 / 亲情号"** 把车辆分享给小号，HA 使用小号登录——
主账号 App 完全不受影响。

### 暂不支持

- ❌ GPS 定位：云端 H5 接口未提供实时车辆位置（原生 App 专属通道），欢迎探索
- ❌ 控制（锁车/解锁/寻车）：涉及指令签名，尚未逆向

## 安装

1. 将 `custom_components/yadea/` 文件夹复制到 Home Assistant 配置目录下的
   `custom_components/yadea/`（HAOS 可用 Samba share 或 File editor）。
2. 重启 Home Assistant。
3. **设置 → 设备与服务 → 添加集成 → 搜索 "Yadea"**。
4. 输入雅迪智行 App 的手机号和密码。
5. （可选）在集成"选项"中调整轮询间隔。

依赖 `pycryptodome`，首次加载时自动安装。

## 自动化示例：低电量提醒

```yaml
automation:
  - alias: 电动车低电量提醒
    trigger:
      - platform: numeric_state
        entity_id: sensor.my_ebike_battery   # 换成你的实体 ID
        below: 20
    action:
      - service: notify.mobile_app_your_phone
        data:
          message: "电动车电量低于 20%，请及时充电"
```

## 技术原理

集成通过逆向雅迪智行 App 的云端通信实现，要点见 [docs/API.md](docs/API.md)：

- 登录走 `tspapph5.yadeaiot.com.cn/prod-api/user/loginByPassword`（纯 JSON）
- 车辆数据走网关签名通道：`gatewaySign` = Base64(AES-128-ECB 加密的
  `gatewayTimestamp={毫秒时间戳}&json={payload}`)
- 车辆列表接口的返回即包含实时 SOC / 剩余里程快照，一次轮询覆盖全部车辆

## 免责声明 / Disclaimer

- 本项目与雅迪（Yadea）官方**无关**，"雅迪/Yadea" 及相关商标归其权利人所有。
  This project is not affiliated with or endorsed by Yadea Technology Group.
- 接口为逆向所得，**雅迪可能随时变更导致失效**，不保证可用性。
- 仅用于读取**自己名下车辆**的数据；请勿用于任何违规用途。
- 请勿将凭据（手机号/密码）分享给他人；轮询间隔请勿设置过低，避免给官方服务
  造成压力。

## License

[MIT](LICENSE)
