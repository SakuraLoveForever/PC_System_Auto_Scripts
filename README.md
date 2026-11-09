# PC System Auto Scripts

一个用于监控电源计划和管理启动项的 Windows 桌面工具。

## 快速开始

运行 [PC_System_Auto_Scripts.exe](dist/PC_System_Auto_Scripts.exe)。移动软件时，请一起复制整个 `dist` 目录。首次运行默认关闭自动监控，可在电源页开启。

## 界面预览

**电源**　查看和切换电源方案，按需开启自动监控。

![电源页面](docs/images/power-page.png)

**启动项**　搜索、添加或删除启动项，删除前自动备份。

![启动项页面](docs/images/startup-page.png)

**设置**　切换主题，管理托盘和本程序的开机自启。

![设置页面](docs/images/settings-page.png)

## 开发

在 Windows 上使用 Python 3.12。

```powershell
py -3.12 -m pip install -r requirements.txt
py -3.12 main.py
py -3.12 tests/run_tests.py
py -3.12 build.py
```

MIT 许可证
