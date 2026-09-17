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
        "zh": "未找到高性能/卓越性能方案，可能需要管理员权限或设备支持",
        "en": "No High/Ultimate Performance plan found. Admin permission or device support may be required.",
    },
    "power.target_missing": {
        "zh": "目标方案已不存在（GUID {guid}…），请重新选择目标或改为自动",
        "en": "The selected target plan no longer exists (GUID {guid}…). Pick a new target or switch to Auto.",
    },
    "power.target_missing_option": {
        "zh": "⚠ 目标已失效 ({guid})",
        "en": "⚠ Missing target ({guid})",
    },
    "power.status_target_missing": {
        "zh": "目标失效",
        "en": "Target Missing",
    },
    "power.check_busy": {
        "zh": "已有一次检查正在进行，请稍候查看结果",
        "en": "A power check is already running; the result will appear here.",
    },
    "power.read_failed": {
        "zh": "读取电源方案失败：{reason}",
        "en": "Could not read power plans: {reason}",
    },
    "power.error_timeout": {
        "zh": "powercfg 响应超时",
        "en": "powercfg timed out",
    },
    "power.error_permission": {
        "zh": "权限不足，请以管理员身份运行",
        "en": "Permission denied — try running as administrator",
    },
    "power.error_unavailable": {
        "zh": "powercfg 不可用",
        "en": "powercfg is unavailable",
    },
    "power.error_not_found": {
        "zh": "指定的电源方案不存在",
        "en": "The requested power plan does not exist",
    },
    "power.error_other": {
        "zh": "未知错误",
        "en": "Unknown error",
    },
    "power.error_not_supported": {
        "zh": "该方案不受支持（Windows 拒绝将其设为当前方案）",
        "en": "Windows refuses to activate this scheme (not supported)",
    },
    "power.skipped_plans": {
        "zh": "已跳过不可用方案：{names}",
        "en": "Skipped unusable plan(s): {names}",
    },
    "power.target_unusable": {
        "zh": "目标「{name}」({guid}) 是 Windows 无法激活的内置模板，请在下方重新选择目标方案（推荐 Auto）",
        "en": "Target '{name}' ({guid}) is a Windows template that cannot be activated — pick another target below (Auto is recommended).",
    },
    "power.create_schemes_btn": {
        "zh": "创建性能方案",
        "en": "Create Plans",
    },
    "power.create_running": {
        "zh": "正在创建缺失的性能方案…",
        "en": "Creating missing performance plans…",
    },
    "power.create_created": {
        "zh": "已创建：{names}",
        "en": "Created: {names}",
    },
    "power.create_existing": {
        "zh": "已存在：{names}",
        "en": "Already present: {names}",
    },
    "power.create_failed_items": {
        "zh": "失败：{names}",
        "en": "Failed: {names}",
    },
    "power.create_failed": {
        "zh": "创建电源方案失败：{reason}",
        "en": "Creating power plans failed: {reason}",
    },
    "power.create_nothing": {
        "zh": "没有需要创建的方案",
        "en": "Nothing to create.",
    },
    "power.monitor_start_failed": {
        "zh": "监控启动失败，请查看日志",
        "en": "Could not start the monitor — see the log file.",
    },
    "power.monitor_stopped_unexpectedly": {
        "zh": "监控线程已意外停止，可用上方开关重新启动",
        "en": "The monitor worker stopped unexpectedly. Use the switch above to restart it.",
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
    "startup.source_all": {
        "zh": "全部来源",
        "en": "All Sources",
    },
    "startup.source_registry": {
        "zh": "当前用户 Run",
        "en": "HKCU Run",
    },
    "startup.source_registry_hklm": {
        "zh": "全局 Run",
        "en": "HKLM Run",
    },
    "startup.source_registry_hklm_wow6432": {
        "zh": "32 位全局 Run",
        "en": "HKLM Wow6432 Run",
    },
    "startup.source_startup_folder": {
        "zh": "用户启动文件夹",
        "en": "Startup Folder",
    },
    "startup.source_startup_folder_common": {
        "zh": "公共启动文件夹",
        "en": "Common Startup",
    },
    # Short tags for the narrow "source" table column; the full label above is
    # used by the filter dropdown and the row tooltip.
    "startup.tag_registry": {
        "zh": "HKCU",
        "en": "HKCU",
    },
    "startup.tag_registry_hklm": {
        "zh": "HKLM",
        "en": "HKLM",
    },
    "startup.tag_registry_hklm_wow6432": {
        "zh": "HKLM32",
        "en": "HKLM32",
    },
    "startup.tag_startup_folder": {
        "zh": "启动夹",
        "en": "Startup",
    },
    "startup.tag_startup_folder_common": {
        "zh": "公共夹",
        "en": "Common",
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
    "startup.location_btn": {
        "zh": "位置",
        "en": "Open",
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
    "startup.opened_location": {
        "zh": "已打开「{name}」所在位置",
        "en": "Opened location for '{name}'.",
    },
    "startup.open_location_failed": {
        "zh": "无法打开「{name}」所在位置",
        "en": "Failed to open location for '{name}'.",
    },
    "startup.fill_both": {
        "zh": "请填写名称和路径",
        "en": "Please enter both name and path.",
    },
    "startup.overwritten": {
        "zh": "已覆盖同名启动项「{name}」",
        "en": "Overwrote the existing startup item '{name}'.",
    },
    "startup.overwrite_cancelled": {
        "zh": "已取消覆盖「{name}」",
        "en": "Cancelled overwriting '{name}'.",
    },
    "startup.unknown_source": {
        "zh": "未知来源「{source}」，已拒绝操作",
        "en": "Unknown source '{source}' — the operation was refused.",
    },
    "startup.read_failed": {
        "zh": "读取启动项失败：{reason}",
        "en": "Could not read startup items: {reason}",
    },
    "startup.backup_ready": {
        "zh": "可在标题栏点「恢复」撤销。",
        "en": "Use Restore in the title bar to undo.",
    },
    "startup.no_backup": {
        "zh": "（未能生成备份）",
        "en": "(no backup could be created)",
    },
    "startup.restore_btn": {
        "zh": "恢复 ({count})",
        "en": "Restore ({count})",
    },
    "startup.restored": {
        "zh": "已恢复「{name}」",
        "en": "Restored '{name}'.",
    },
    "startup.restore_failed": {
        "zh": "恢复「{name}」失败",
        "en": "Failed to restore '{name}'.",
    },
    "startup.nothing_to_restore": {
        "zh": "没有可恢复的启动项",
        "en": "Nothing to restore.",
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

    # Dialogs
    "dialog.overwrite_title": {
        "zh": "覆盖同名启动项？",
        "en": "Overwrite startup item?",
    },
    "dialog.overwrite_body": {
        "zh": "名为「{name}」的启动项已存在。\n\n现有命令：\n{old}\n\n新命令：\n{new}\n\n继续将覆盖现有条目。",
        "en": "A startup item named '{name}' already exists.\n\nCurrent command:\n{old}\n\nNew command:\n{new}\n\nContinuing will overwrite the existing entry.",
    },
    "dialog.remove_title": {
        "zh": "删除启动项？",
        "en": "Remove startup item?",
    },
    "dialog.remove_body": {
        "zh": "将从系统中移除以下启动项：\n\n名称：{name}\n来源：{source}\n命令/文件：\n{target}\n\n删除前会自动保存备份，可用「恢复」撤销。",
        "en": "The following startup item will be removed:\n\nName: {name}\nSource: {source}\nCommand/file:\n{target}\n\nA backup is saved first so this can be undone with Restore.",
    },
    "dialog.restore_title": {
        "zh": "恢复启动项？",
        "en": "Restore startup item?",
    },
    "dialog.restore_body": {
        "zh": "将恢复最近删除的启动项：\n\n名称：{name}\n位置：\n{target}",
        "en": "The most recently removed startup item will be restored:\n\nName: {name}\nLocation:\n{target}",
    },

    # First-run setup
    "setup.title": {
        "zh": "首次运行设置",
        "en": "First-run setup",
    },
    "setup.current": {
        "zh": "当前电源方案：{name}",
        "en": "Current power plan: {name}",
    },
    "setup.explain": {
        "zh": "本工具可以在方案偏离目标时自动切换。读取不会修改系统；只有你启用监控或手动切换/创建方案时才会改动电源设置。",
        "en": "This tool can switch plans automatically when they drift from your target. Reading never changes the system — power settings only change when you enable monitoring or explicitly switch/create a plan.",
    },
    "setup.read_error": {
        "zh": "注意：{reason}",
        "en": "Note: {reason}",
    },
    "setup.enable_monitor": {
        "zh": "启用自动监控",
        "en": "Enable auto monitoring",
    },
    "setup.use_auto": {
        "zh": "自动选择目标",
        "en": "Auto target",
    },
    "setup.pick_plan": {
        "zh": "手动选择目标",
        "en": "Pick target",
    },
    "setup.pick_title": {
        "zh": "选择目标电源方案",
        "en": "Choose target power plan",
    },
    "setup.skip": {
        "zh": "稍后再说",
        "en": "Later",
    },
    "setup.done": {
        "zh": "设置已保存",
        "en": "Setup saved.",
    },
    "setup.no_performance_plan": {
        "zh": "没有可用的高性能方案，可先用「创建性能方案」",
        "en": "No performance plan available — try 'Create Plans' first.",
    },
}

DEFAULT_LANG = "zh"
SUPPORTED_LANGS = ["zh", "en"]
LANG_LABELS = {"zh": "中文", "en": "English"}


class I18n:
    def __init__(self, lang: str = DEFAULT_LANG):
        self.lang = lang

    def has(self, key: str) -> bool:
        return key in T

    def t(self, key: str, **kwargs) -> str:
        entry = T.get(key, {})
        text = entry.get(self.lang) or entry.get("en", key)
        if kwargs:
            text = text.format(**kwargs)
        return text
