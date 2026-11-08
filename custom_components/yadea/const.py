"""Constants for the Yadea integration."""
DOMAIN = "yadea"

CONF_ACCOUNT = "account"
CONF_PASSWORD = "password"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TRACK_INTERVAL = "track_interval"
CONF_AUTO_RELOGIN = "auto_relogin"

DEFAULT_SCAN_INTERVAL = 60  # seconds
DEFAULT_TRACK_INTERVAL = 300  # 轨迹/里程统计刷新间隔（秒）
DEFAULT_AUTO_RELOGIN = False  # 雅迪单会话：自动重登会把手机 App 顶下线

# ---- 云端网关（逆向自雅迪智行 H5 前端, 见 RESEARCH_NOTES.md） ----
WG_BASE = "https://tspapph5.yadeaiot.com.cn/prod-wg/api/app"
USER_BASE = "https://tspapph5.yadeaiot.com.cn/prod-api/user"
APP_ID = "cfb00038ddac4c9ebc85821694bbd3fa"
SECRET = b"RFC1J02e116b45a0"  # AES-128-ECB key
USER_AGENT = "YadeaApp/7.3.0"

CODE_OK = "000000"

PLATFORMS = ["sensor", "image"]
