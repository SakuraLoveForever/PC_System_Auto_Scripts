# PC System Auto Scripts

Windows 桌面工具，提供电源计划自动监控与启动项管理。主窗默认 580 × 290，通过顶部标签切换电源、启动项和设置。

## 界面预览

### 电源

![电源页面](docs/images/power-page.png)

### 启动项

![启动项页面](docs/images/startup-page.png)

### 设置

![设置页面](docs/images/settings-page.png)

## 功能

- **电源计划** — 刷新状态只读；可单独切换当前方案，或按目标策略立即纠正。开启自动监控后按设置的间隔检查并纠正。
- **启动项管理** — 搜索、筛选、排序和调整列宽。选中一行可打开位置或删除，点击「添加」展开新建表单。
- **删除可撤销** — 确认时显示名称、来源和命令；备份成功后才删除。“恢复”可以还原该条备份，同名目标已存在时不会覆盖。
- **外观** — 默认使用紧凑的系统风格；在设置页可切换主题，包括雷姆主题。
- **系统托盘** — 可设置关闭窗口时保留在托盘，以及随 Windows 启动时直接进入托盘。
- **单实例运行** — 命名互斥体保证只运行一个实例，重复启动会唤起已有窗口
- **首次运行引导** — 自动监控默认关闭，启用后才会按策略自动切换。

## 快速上手

直接运行 `dist/PC_System_Auto_Scripts.exe`，无需安装 Python 或任何依赖。

请保留 exe 同目录下的 `_internal` 文件夹。程序直接从该目录加载运行资源，
启动时无需解压到系统临时目录；移动或复制软件时请一起移动整个 `dist` 文件夹。

- 首次运行会生成 `config.json`（默认与 exe 同目录；该目录不可写时自动改用 `%LOCALAPPDATA%\PC_System_Auto_Scripts\`）
- 首次运行会显示引导。可以选择目标策略并启用自动监控，也可以跳过。
- 关闭窗口默认隐藏到系统托盘，右键托盘图标可退出（可在设置页关闭此行为）。
- 双击 exe 显示主界面；开机自启动通过 `--startup` 参数进入托盘。
- 已保存的监控偏好、目标方案和托盘设置会继续使用。

## 技术栈

| 层级 | 选型 |
|------|------|
| UI 框架 | [customtkinter](https://github.com/TomSchimansky/CustomTkinter) 5.2.2 |
| 系统托盘 | [pystray](https://github.com/moses-palmer/pystray) 0.19.5 |
| 打包 | [PyInstaller](https://pyinstaller.org/) 6.20.0 |
| 图标生成 | Pillow 12.2.0 |
| 拼音排序 | pypinyin 0.55.0 |
| 语言 | Python 3.12+（仅 Windows：使用 `winreg` 与 `ctypes.windll`） |

## 项目结构

```
├── main.py               # UI、配置、单实例、托盘、监控协调
├── power_manager.py      # 电源方案读取 / 判定 / 切换 / 监控线程
├── startup_manager.py    # 启动项读取 / 增删 / 备份与恢复
├── styles.py             # 界面主题
├── i18n.py               # 中文文案
├── build.py              # PyInstaller 打包脚本
├── requirements.txt      # 已验证依赖版本
├── config.json           # 运行配置（首次运行自动生成）
├── tests/                # 回归测试（见下）
└── designs/              # 设计参考图
```

## 开发

```bash
# 安装依赖
pip install -r requirements.txt

# 运行
python main.py

# 回归测试（GUI 在独立桌面运行，不会在当前桌面弹出；系统读写使用测试数据）
python tests/run_tests.py
# 或
python -m unittest discover -s tests -v

# 生成实际 Tk 窗口预览（样例数据，不显示在当前桌面）
python tools/capture_ui.py shot/startup.png --page startup

# 打包为 exe
python build.py            # 保留已有 config.json / startup_backups / 日志
python build.py --clean    # 额外删除生成的 .spec
```

打包产物输出到 `dist/PC_System_Auto_Scripts.exe`。**重新打包不会删除** `dist/config.json`、`dist/startup_backups/` 和 `dist/pc_auto_scripts.log`（旧的构建脚本会清空整个 `dist`，已修正）。

## 配置

`config.json`（默认与 exe 同目录；目录不可写时放在 `%LOCALAPPDATA%\PC_System_Auto_Scripts\`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `ui_theme` | string | 设置页选择的界面主题；未选择时使用系统风格 |
| `language` | string | 固定为 `zh`（中文），旧值自动迁移 |
| `monitor_enabled` | bool | 是否启用自动监控（关闭后重启仍保持关闭） |
| `check_interval` | int | 电源检查间隔（秒），范围 10–3600 |
| `target_guid` | string | 目标电源方案 GUID；留空 = 自动选择（卓越性能 → 高性能），GUID 不存在时会明确提示而不是偷偷换目标 |
| `minimize_to_tray` | bool | 关闭窗口时隐藏到托盘 |
| `start_to_tray` | bool | 启动后直接进入托盘（默认 true） |
| `col_widths` | float[4] | 启动项列表列宽比例，兼容旧配置 |
| `setup_completed` | bool | 是否已完成首次运行引导 |

配置写入采用“临时文件 + 原子替换”；文件损坏时会保留为 `config.json.corrupt` 并回退默认值，保存失败会在界面提示。

## 运行数据与日志

| 路径 | 说明 |
|------|------|
| `config.json` | 用户配置 |
| `pc_auto_scripts.log` | 滚动日志（512 KB × 3），排查失败原因用 |
| `startup_backups/` | 删除启动项前的文件备份 |
| `startup_backups.json` | 可撤销删除的备份台账 |

也可用环境变量 `PC_AUTO_SCRIPTS_DATA_DIR` 指定数据目录（测试用）。

## 启动项说明

- 覆盖范围：`HKCU\...\Run`、`HKLM\...\Run`、`HKLM\...\WOW6432Node\...\Run`、当前用户启动文件夹、公共启动文件夹
- 启动文件夹内的条目以**完整路径**为标识，所以 `demo.cmd` 与 `demo.lnk` 是两条互不影响的项目
- 删除前会弹出确认框，显示名称、来源、命令/文件路径；确认后先备份再删除
- 删除 HKLM/公共启动文件夹的条目可能需要管理员权限，失败时会显示原因
- “显示启用状态/一键禁用”尚未实现：`Run` 值或启动文件存在并不等于 Windows 实际允许它启动

## 已知限制

- 电源方案读取依赖 `powercfg`；超时、权限不足、无可读数据会分别提示不同原因
- 自动监控只在运行期间生效，退出后不再纠正电源方案
- 不同 Windows 显示缩放比例下的长路径尚未做完整实机验收

## License

MIT
