"""把 4 个深色视图合并进 HA 存储，并统一修补所有 heading 卡的标题配色。

背景：HA heading 卡的文字节点是 h2/h3.heading，颜色来自 --ha-heading-card-* 变量，
旧的 `.title` 选择器不生效（浅色主题下会深底深字）。本脚本对全部视图（含旗舰版）
重写 heading 的 card_mod，保证 5 页一致可读。
"""
import json
import yaml

STORAGE = r"\\192.168.3.166\config\.storage\lovelace.yadea"
REPO_YAML = "dashboard-example.yaml"

ICON_COLORS = {
    "mdi:transmission-tower": "#fbbf24",
    "mdi:information-outline": "#64748b",
}
DEFAULT_ICON = "#60a5fa"


def heading_style(icon):
    color = ICON_COLORS.get(icon, DEFAULT_ICON)
    return (
        "ha-card {\n"
        "  --ha-heading-card-title-color: #e2e8f0;\n"
        "  --ha-heading-card-subtitle-color: #94a3b8;\n"
        "  --ha-heading-card-title-font-weight: 600;\n"
        "  --ha-heading-card-subtitle-font-weight: 600;\n"
        "}\n"
        ".content { letter-spacing: .6px; }\n"
        f".content ha-icon {{ color: {color} !important; }}"
    )


def iter_cards(view):
    for c in view.get("cards", []) or []:
        yield c
        for sub in c.get("cards", []) or []:
            yield sub
    for s in view.get("sections", []) or []:
        for c in s.get("cards", []) or []:
            yield c
            for sub in c.get("cards", []) or []:
                yield sub


d = json.load(open(STORAGE, encoding="utf-8"))
cfg = d["data"]["config"]

new_views = {v["path"]: v for v in yaml.safe_load(open("views-dark.yaml", encoding="utf-8"))}
order = ["classic", "compact", "glance", "advanced", "pro"]
cur = {v.get("path"): v for v in cfg["views"]}

merged = []
for path in order:
    if path in new_views:
        merged.append(new_views[path])
    elif path in cur:
        merged.append(cur[path])
for v in cfg["views"]:
    if v.get("path") not in order:
        merged.append(v)
cfg["views"] = merged

# 统一修补 heading 卡配色（5 页一致）
patched = 0
for v in merged:
    for c in iter_cards(v):
        if c.get("type") == "heading":
            c.setdefault("card_mod", {})["style"] = heading_style(c.get("icon"))
            patched += 1

json.dump(d, open(STORAGE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("已写入 HA | 视图:", [v["title"] for v in merged], "| heading 修补:", patched)

# 用同一份配置重写仓库示例，保持 HA 与仓库零漂移
class Dumper(yaml.SafeDumper):
    pass


def _str_rep(dumper, data):
    return dumper.represent_scalar(
        "tag:yaml.org,2002:str", data, style="|" if "\n" in data else None
    )


Dumper.add_representer(str, _str_rep)

head = (
    "# 雅迪智行 · Home Assistant 仪表盘示例（5 视图统一深色主题）\n"
    "# 依赖：card-mod；进阶版另需 mushroom + mini-graph-card\n"
    "# 用法：整段贴进「设置 → 仪表盘 → 添加仪表盘 → YAML 模式」，替换实体 ID 前缀即可\n"
)
text = head + yaml.dump(
    {"title": cfg.get("title", "雅迪电动车"), "views": cfg["views"]},
    Dumper=Dumper,
    allow_unicode=True,
    sort_keys=False,
    width=100000,
    default_flow_style=False,
    indent=2,
)
open(REPO_YAML, "w", encoding="utf-8").write(text)
print("仓库示例已重建，views =", len(cfg["views"]))
