"""Chinese interface strings."""

from __future__ import annotations

from typing import Dict

# All translatable strings
T: Dict[str, Dict[str, str]] = {
    "app.title": {
        "zh": "PC 系统自动脚本"
    },
    "app.subtitle": {
        "zh": "电源计划监控 & 启动项管理"
    },
    "sidebar.collapse": {
        "zh": "收起"
    },
    "sidebar.settings": {
        "zh": "设置"
    },
    "sidebar.expand": {
        "zh": "展开"
    },
    "power.title": {
        "zh": "电源计划监控"
    },
    "power.active_plan": {
        "zh": "当前方案"
    },
    "power.target_plan": {
        "zh": "目标方案"
    },
    "power.interval": {
        "zh": "检查间隔"
    },
    "power.seconds": {
        "zh": "秒"
    },
    "power.check_now": {
        "zh": "立即检查"
    },
    "power.auto_monitor": {
        "zh": "自动监控"
    },
    "power.status_ok": {
        "zh": "正常"
    },
    "power.status_needs_fix": {
        "zh": "需修复"
    },
    "power.status_error": {
        "zh": "错误"
    },
    "power.status_checking": {
        "zh": "检查中..."
    },
    "power.unable_detect": {
        "zh": "无法检测"
    },
    "power.switched": {
        "zh": "已从「{from_}」切换至「{to}」"
    },
    "power.already_ok": {
        "zh": "电源方案正确：{name}"
    },
    "power.switch_failed": {
        "zh": "切换失败：{name}"
    },
    "power.no_target": {
        "zh": "未找到高性能/卓越性能方案，可能需要管理员权限或设备支持"
    },
    "power.target_missing": {
        "zh": "目标方案已不存在（GUID {guid}…），请重新选择目标或改为自动"
    },
    "power.target_missing_option": {
        "zh": "⚠ 目标已失效 ({guid})"
    },
    "power.status_target_missing": {
        "zh": "目标失效"
    },
    "power.check_busy": {
        "zh": "已有一次检查正在进行，请稍候查看结果"
    },
    "power.read_failed": {
        "zh": "读取电源方案失败：{reason}"
    },
    "power.error_timeout": {
        "zh": "powercfg 响应超时"
    },
    "power.error_permission": {
        "zh": "权限不足，请以管理员身份运行"
    },
    "power.error_unavailable": {
        "zh": "powercfg 不可用"
    },
    "power.error_not_found": {
        "zh": "指定的电源方案不存在"
    },
    "power.error_other": {
        "zh": "未知错误"
    },
    "power.error_not_supported": {
        "zh": "该方案不受支持（Windows 拒绝将其设为当前方案）"
    },
    "power.skipped_plans": {
        "zh": "已跳过不可用方案：{names}"
    },
    "power.target_unusable": {
        "zh": "目标「{name}」({guid}) 是 Windows 无法激活的内置模板，请在下方重新选择目标方案（推荐 Auto）"
    },
    "power.create_schemes_btn": {
        "zh": "创建性能方案"
    },
    "power.create_running": {
        "zh": "正在创建缺失的性能方案…"
    },
    "power.create_created": {
        "zh": "已创建：{names}"
    },
    "power.create_existing": {
        "zh": "已存在：{names}"
    },
    "power.create_failed_items": {
        "zh": "失败：{names}"
    },
    "power.create_failed": {
        "zh": "创建电源方案失败：{reason}"
    },
    "power.create_nothing": {
        "zh": "没有需要创建的方案"
    },
    "power.monitor_start_failed": {
        "zh": "监控启动失败，请查看日志"
    },
    "power.monitor_stopped_unexpectedly": {
        "zh": "监控线程已意外停止，可用上方开关重新启动"
    },
    "power.monitor_started": {
        "zh": "电源计划监控已启动"
    },
    "power.monitor_stopped": {
        "zh": "电源计划监控已停止"
    },
    "startup.title": {
        "zh": "启动项管理"
    },
    "startup.entries": {
        "zh": "个条目"
    },
    "startup.col_name": {
        "zh": "名称"
    },
    "startup.col_path": {
        "zh": "路径"
    },
    "startup.col_source": {
        "zh": "来源"
    },
    "startup.col_action": {
        "zh": "操作"
    },
    "startup.source_all": {
        "zh": "全部来源"
    },
    "startup.source_registry": {
        "zh": "当前用户 Run"
    },
    "startup.source_registry_hklm": {
        "zh": "全局 Run"
    },
    "startup.source_registry_hklm_wow6432": {
        "zh": "32 位全局 Run"
    },
    "startup.source_startup_folder": {
        "zh": "用户启动文件夹"
    },
    "startup.source_startup_folder_common": {
        "zh": "公共启动文件夹"
    },
    "startup.tag_registry": {
        "zh": "HKCU"
    },
    "startup.tag_registry_hklm": {
        "zh": "HKLM"
    },
    "startup.tag_registry_hklm_wow6432": {
        "zh": "HKLM32"
    },
    "startup.tag_startup_folder": {
        "zh": "启动夹"
    },
    "startup.tag_startup_folder_common": {
        "zh": "公共夹"
    },
    "startup.name_placeholder": {
        "zh": "条目名称"
    },
    "startup.path_placeholder": {
        "zh": "程序路径..."
    },
    "startup.add_btn": {
        "zh": "＋ 添加"
    },
    "startup.add_self_btn": {
        "zh": "＋ 添加本程序"
    },
    "startup.remove_self_btn": {
        "zh": "－ 移除本程序"
    },
    "startup.remove_self": {
        "zh": "已从启动项移除本程序"
    },
    "startup.refresh_btn": {
        "zh": "刷新"
    },
    "startup.remove_btn": {
        "zh": "删除"
    },
    "startup.location_btn": {
        "zh": "位置"
    },
    "startup.empty": {
        "zh": "未找到启动项"
    },
    "startup.added": {
        "zh": "已添加「{name}」到启动项"
    },
    "startup.add_failed": {
        "zh": "添加启动项失败"
    },
    "startup.removed": {
        "zh": "已移除「{name}」"
    },
    "startup.remove_failed": {
        "zh": "移除「{name}」失败"
    },
    "startup.opened_location": {
        "zh": "已打开「{name}」所在位置"
    },
    "startup.open_location_failed": {
        "zh": "无法打开「{name}」所在位置"
    },
    "startup.fill_both": {
        "zh": "请填写名称和路径"
    },
    "startup.overwritten": {
        "zh": "已覆盖同名启动项「{name}」"
    },
    "startup.overwrite_cancelled": {
        "zh": "已取消覆盖「{name}」"
    },
    "startup.unknown_source": {
        "zh": "未知来源「{source}」，已拒绝操作"
    },
    "startup.read_failed": {
        "zh": "读取启动项失败：{reason}"
    },
    "startup.backup_ready": {
        "zh": "可在标题栏点「恢复」撤销。"
    },
    "startup.no_backup": {
        "zh": "（未能生成备份）"
    },
    "startup.restore_btn": {
        "zh": "恢复 ({count})"
    },
    "startup.restored": {
        "zh": "已恢复「{name}」"
    },
    "startup.restore_failed": {
        "zh": "恢复「{name}」失败"
    },
    "startup.nothing_to_restore": {
        "zh": "没有可恢复的启动项"
    },
    "startup.added_self": {
        "zh": "已将本程序添加到启动项"
    },
    "startup.interval_invalid": {
        "zh": "请输入有效的间隔秒数"
    },
    "startup.interval_set": {
        "zh": "检查间隔已设为 {secs} 秒"
    },
    "tray.minimize_to_tray": {
        "zh": "关闭时隐藏到托盘"
    },
    "tray.show": {
        "zh": "显示窗口"
    },
    "tray.check": {
        "zh": "立即检查电源"
    },
    "tray.exit": {
        "zh": "退出"
    },
    "compact.toggle": {
        "zh": "精简"
    },
    "compact.full": {
        "zh": "完整"
    },
    "compact.current_plan": {
        "zh": "当前"
    },
    "compact.switch_power": {
        "zh": "切换电源"
    },
    "general.ok": {
        "zh": "确定"
    },
    "general.cancel": {
        "zh": "取消"
    },
    "general.apply": {
        "zh": "应用"
    },
    "dialog.overwrite_title": {
        "zh": "覆盖同名启动项？"
    },
    "dialog.overwrite_body": {
        "zh": "名为「{name}」的启动项已存在。\n\n现有命令：\n{old}\n\n新命令：\n{new}\n\n继续将覆盖现有条目。"
    },
    "dialog.remove_title": {
        "zh": "删除启动项？"
    },
    "dialog.remove_body": {
        "zh": "将从系统中移除以下启动项：\n\n名称：{name}\n来源：{source}\n命令/文件：\n{target}\n\n删除前会自动保存备份，可用「恢复」撤销。"
    },
    "dialog.restore_title": {
        "zh": "恢复启动项？"
    },
    "dialog.restore_body": {
        "zh": "将恢复最近删除的启动项：\n\n名称：{name}\n位置：\n{target}"
    },
    "setup.title": {
        "zh": "首次运行设置"
    },
    "setup.current": {
        "zh": "当前电源方案：{name}"
    },
    "setup.explain": {
        "zh": "本工具可以在方案偏离目标时自动切换。读取不会修改系统；只有你启用监控或手动切换/创建方案时才会改动电源设置。"
    },
    "setup.read_error": {
        "zh": "注意：{reason}"
    },
    "setup.enable_monitor": {
        "zh": "启用自动监控"
    },
    "setup.use_auto": {
        "zh": "自动选择目标"
    },
    "setup.pick_plan": {
        "zh": "手动选择目标"
    },
    "setup.pick_title": {
        "zh": "选择目标电源方案"
    },
    "setup.skip": {
        "zh": "稍后再说"
    },
    "setup.done": {
        "zh": "设置已保存"
    },
    "setup.no_performance_plan": {
        "zh": "没有可用的高性能方案，可先用「创建性能方案」"
    }
}

DEFAULT_LANG = "zh"
SUPPORTED_LANGS = ["zh"]
LANG_LABELS = {"zh": "中文"}


class I18n:
    def __init__(self, lang: str = DEFAULT_LANG):
        self.lang = DEFAULT_LANG

    def has(self, key: str) -> bool:
        return key in T

    def t(self, key: str, **kwargs) -> str:
        entry = T.get(key, {})
        text = entry.get("zh", key)
        if kwargs:
            text = text.format(**kwargs)
        return text
