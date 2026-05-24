"""Internationalization: Chinese / English translations."""

from __future__ import annotations

from typing import Dict

# All translatable strings
T: Dict[str, Dict[str, str]] = {
    # App title / branding
    "app.title": {
        "zh": "PC 系统自动脚本",
        "en": "PC System Auto Scripts",
    },
    "app.subtitle": {
        "zh": "电源计划监控 & 启动项管理",
        "en": "Power Plan Monitor & Startup Manager",
    },

    # Sidebar
    "sidebar.style": {
        "zh": "界面风格",
        "en": "Style",
    },
    "sidebar.language": {
        "zh": "语言",
        "en": "Language",
    },
    "sidebar.collapse": {
        "zh": "收起",
        "en": "Collapse",
    },
    "sidebar.appearance": {
        "zh": "外观",
        "en": "APPEARANCE",
    },
    "sidebar.settings": {
        "zh": "设置",
        "en": "SETTINGS",
    },
    "sidebar.expand": {
        "zh": "展开",
        "en": "Expand",
    },

    # Power card
    "power.title": {
        "zh": "电源计划监控",
        "en": "Power Plan Monitor",
    },
    "power.active_plan": {
        "zh": "当前方案",
        "en": "Active Plan",
    },
    "power.target_plan": {
        "zh": "目标方案",
        "en": "Target Plan",
    },
    "power.interval": {
        "zh": "检查间隔",
        "en": "Interval",
    },
    "power.seconds": {
        "zh": "秒",
        "en": "s",
    },
    "power.check_now": {
        "zh": "立即检查",
        "en": "Check Now",
    },
    "power.auto_monitor": {
        "zh": "自动监控",
        "en": "Auto Monitor",
    },
    "power.status_ok": {
        "zh": "正常",
        "en": "OK",
    },
    "power.status_needs_fix": {
        "zh": "需修复",
        "en": "Needs Fix",
    },
    "power.status_error": {
        "zh": "错误",
        "en": "Error",
    },
    "power.status_checking": {
        "zh": "检查中...",
        "en": "Checking...",
    },
    "power.unable_detect": {
        "zh": "无法检测",
        "en": "Unable to detect",
    },
    "power.switched": {
        "zh": "已从「{from_}」切换至「{to}」",
        "en": "Switched from '{from_}' to '{to}'",
    },
    "power.already_ok": {
        "zh": "电源方案正确：{name}",
        "en": "Power plan is correct: {name}",
    },
    "power.switch_failed": {
        "zh": "切换失败：{name}",
        "en": "Failed to switch from '{name}'",
    },
    "power.no_target": {
        "zh": "未找到高性能方案，请通过 powercfg 创建",
        "en": "No High Performance plan found. Create one via powercfg.",
    },
    "power.monitor_started": {
        "zh": "电源计划监控已启动",
        "en": "Power plan monitor started.",
    },
    "power.monitor_stopped": {
        "zh": "电源计划监控已停止",
        "en": "Power plan monitor stopped.",
    },

    # Startup card
    "startup.title": {
        "zh": "启动项管理",
        "en": "Startup Items",
    },
    "startup.entries": {
        "zh": "个条目",
        "en": " entries",
    },
    "startup.col_name": {
        "zh": "名称",
        "en": "Name",
    },
    "startup.col_path": {
        "zh": "路径",
        "en": "Path",
    },
    "startup.col_source": {
        "zh": "来源",
        "en": "Source",
    },
    "startup.col_action": {
        "zh": "操作",
        "en": "Action",
    },
    "startup.name_placeholder": {
        "zh": "条目名称",
        "en": "Entry name",
    },
    "startup.path_placeholder": {
        "zh": "程序路径...",
        "en": "Program path...",
    },
    "startup.add_btn": {
        "zh": "＋ 添加",
        "en": "+ Add",
    },
    "startup.add_self_btn": {
        "zh": "＋ 添加本程序",
        "en": "+ This App",
    },
    "startup.remove_self_btn": {
        "zh": "－ 移除本程序",
        "en": "- This App",
    },
    "startup.remove_self": {
        "zh": "已从启动项移除本程序",
        "en": "Removed this app from startup.",
    },
    "startup.refresh_btn": {
        "zh": "刷新",
        "en": "Refresh",
    },
    "startup.remove_btn": {
        "zh": "删除",
        "en": "Del",
    },
    "startup.empty": {
        "zh": "未找到启动项",
        "en": "No startup items found.",
    },
    "startup.added": {
        "zh": "已添加「{name}」到启动项",
        "en": "Added '{name}' to startup.",
    },
    "startup.add_failed": {
        "zh": "添加启动项失败",
        "en": "Failed to add startup item.",
    },
    "startup.removed": {
        "zh": "已移除「{name}」",
        "en": "Removed '{name}'.",
    },
    "startup.remove_failed": {
        "zh": "移除「{name}」失败",
        "en": "Failed to remove '{name}'.",
    },
    "startup.fill_both": {
        "zh": "请填写名称和路径",
        "en": "Please enter both name and path.",
    },
    "startup.added_self": {
        "zh": "已将本程序添加到启动项",
        "en": "Added this app to startup.",
    },
    "startup.interval_invalid": {
        "zh": "请输入有效的间隔秒数",
        "en": "Invalid interval value.",
    },
    "startup.interval_set": {
        "zh": "检查间隔已设为 {secs} 秒",
        "en": "Check interval set to {secs}s.",
    },

    # Close behavior
    "tray.minimize_to_tray": {
        "zh": "关闭时隐藏到托盘",
        "en": "Minimize to tray on close",
    },

    # Tray
    "tray.show": {
        "zh": "显示窗口",
        "en": "Show",
    },
    "tray.check": {
        "zh": "立即检查电源",
        "en": "Check Power Now",
    },
    "tray.exit": {
        "zh": "退出",
        "en": "Exit",
    },

    # Compact mode
    "compact.toggle": {
        "zh": "精简",
        "en": "Compact",
    },
    "compact.full": {
        "zh": "完整",
        "en": "Full",
    },
    "compact.current_plan": {
        "zh": "当前",
        "en": "Current",
    },
    "compact.switch_power": {
        "zh": "切换电源",
        "en": "Switch Power",
    },
    "compact.switch_language": {
        "zh": "切换语言",
        "en": "Switch Language",
    },
    "compact.switch_style": {
        "zh": "切换外观",
        "en": "Switch Style",
    },

    # General
    "general.ok": {
        "zh": "确定",
        "en": "OK",
    },
    "general.cancel": {
        "zh": "取消",
        "en": "Cancel",
    },
    "general.apply": {
        "zh": "应用",
        "en": "Apply",
    },
}

DEFAULT_LANG = "zh"
SUPPORTED_LANGS = ["zh", "en"]
LANG_LABELS = {"zh": "中文", "en": "English"}


class I18n:
    def __init__(self, lang: str = DEFAULT_LANG):
        self.lang = lang

    def t(self, key: str, **kwargs) -> str:
        entry = T.get(key, {})
        text = entry.get(self.lang) or entry.get("en", key)
        if kwargs:
            text = text.format(**kwargs)
        return text
