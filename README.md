# Wuwa Tappo Plus

Wuwa Tappo 的第三方本地增强工具，提供自动开箱、余额提醒、主窗口保持打开、每日键鼠输入统计和房间历史。由独立项目维护，与原版桌宠分别安装和管理。

**普通用户下载 Windows Release 中的单文件 EXE，双击 `WuwaTappoPlus.exe`。日常功能在桌宠的「设置 → Wuwa Tappo Plus」中调整。**

## 功能

| 功能 | 说明 |
| --- | --- |
| 自动开箱 | 宝箱到期、开启按钮出现且余额达到阈值时，点击真实按钮 |
| 余额提醒 | 余额偏低时显示气泡，补充宝箱倒计时或已刷新提示 |
| 气泡动画 | 可切换跳动提醒与静态显示；气泡随桌宠移动并避让屏幕边缘 |
| 主窗口保持打开 | 点击其他窗口时不自动收起背包／设置面板，仍可手动关闭 |
| 每日键鼠统计 | 按桌宠原生计数口径统计键盘与鼠标输入合计，展示昨日数据和最近 7 天柱状图 |
| 房间历史 | 联机页记录进过的房间号和房主，点一下即可回到该房间 |

自动开箱调用页面里的按钮，不构造奖励接口请求，不修改应用二进制文件或宝箱冷却时间。房间历史同样只填入真实输入框并点击「加入房间」按钮。

## 运行环境

- Windows 10 / 11 x64，已安装并登录 Wuwa Tappo。
- 允许通过 UAC 授予安装／卸载所需的管理员权限。
- 单文件 EXE 已内嵌运行环境和注入资源，无需安装 Python，也无需额外的 `_internal/` 文件夹。
- 源码用户需要 Python 3.11+；本地已有 `runtime/` 时可以继续使用，运行时不纳入 Git。

本项目使用 Python 标准库，无需安装第三方 Python 依赖。Node.js 仅用于开发时运行 JavaScript 测试。

## 快速开始

1. 下载 `WuwaTappoPlus.exe`，放到固定目录，双击 **`WuwaTappoPlus.exe`**。
2. 在终端管理面板按 `1` 安装 / 修复，按提示授予管理员权限。
3. 等待安装完成。过程中会配置 WebView2 调试策略、自启和看门狗，并启动或重启桌宠。
4. 打开桌宠的 **设置 → Wuwa Tappo Plus**，调整功能开关。

安装完成后可以关闭终端面板，后台会无控制台运行。单文件启动时会把必要组件释放到临时目录，进程退出后清理；个人数据不放在临时目录。打开终端面板和只读检查无需提权；安装／卸载时再请求权限。移动程序目录后，请重新安装／修复，以更新自启和计划任务路径。

终端面板沿用原来的数字菜单。运行状态和诊断检查可使用命令行。源码用户可以运行 `python launch.py help`。

```powershell
.\WuwaTappoPlus.exe --output doctor.txt doctor
```

## 桌宠设置中的 Plus 开关

| 开关 | 开启时 | 关闭时 |
| --- | --- | --- |
| 自动开箱 | 到期且余额充足时自动点击开启按钮 | 暂停自动点击，保留后台、提醒和输入统计 |
| 余额提醒气泡 | 余额不足时显示提醒和倒计时 | 隐藏气泡，保留余额日志提醒 |
| 气泡动画 | 显示弹出和跳动动画 | 保留静态气泡 |
| 主窗口保持打开 | 点击外部时保留主面板 | 恢复原应用的点击外部收起行为 |

四个开关默认开启，正常连接时约一秒生效，自动保存到 `config.toml`。保存失败会提示并恢复原设置；后台失联时，开关暂时不可用。

**关闭自动开箱只是暂停功能，不等于卸载 Plus。** 重新开启即可继续，无需重启桌宠。

## 每日键鼠输入统计

设置页的「昨日键鼠输入」展示昨日合计次数，下方柱状图展示最近 7 天：

- 按日期从左到右排列，今天使用深色柱；悬停可查看完整日期和精确次数。
- `0` 表示已采样但没有新增输入；`—` 和斜纹表示没有数据。
- 使用桌宠原生的累计键鼠输入值，按增量记录，**不是开箱次数，也不是字符余额**。
- 键盘和鼠标合并统计，目前不分别拆分。

首次启用只建立基线，不把之前的累计值算进今天，因此无法补出启用前的昨日数据。统计按电脑本地日期分组；跨日停机的增量无法准确分配，不会补算到某一天。切换账号或累计值回退时会重新建立基线。

数据持久化在 **`reports/input.sqlite3`**，后台重启后继续累计，卸载也会保留。界面只显示 7 天，更早的数据仍保存在数据库中。统计保存每日计数、累计值基线和账号标识的哈希，不记录具体按键内容。

## 房间历史

打开功能面板的「联机」标签，加入房间后会自动记录房间号、房主和进入次数。未加入房间时，「最近房间」列表显示在「加入房间」下方：

- 列表按最近访问排序，显示房间号、房主、进入次数与相对时间；同一房间只留一条记录。
- 未加入房间时，点一行会填入房间号，等待输入同步后点击原生「加入房间」按钮。在房间中时隐藏历史列表。
- 行尾的 `×` 删除一条记录，只删除本机记录，不影响应用内的任何数据。
- 默认保留最近 20 条，由 `config.toml` 的 `[rooms] history` 控制。

记录的是房间号和房主昵称，不保存密码或房间内成员列表。刚进房时房主名字可能比房间号晚一秒渲染，记录会先写房间号，名字出现后补上，不会重复计一次进入。

## 安装、修复与卸载

重复安装用于修复连接、自启动和计划任务，不会重置功能开关。卸载会停止后台、移除本项目的策略、自启动和看门狗，重启正在运行的桌宠以清空页面脚本；个人配置与数据库继续保留。

EXE 版数据目录为 `%LOCALAPPDATA%\WuwaTappoPlus`；源码版继续使用仓库根目录。升级时先卸载旧后台，替换 EXE，再执行安装／修复。备份时先卸载以停止写入，再复制数据目录中的 `config.toml` 和 `reports/`。


## 命令行

EXE 由 Python 直接显示与原 BAT 相同的终端面板：`1` 安装／修复、`2` 卸载、`0` 关闭。带命令启动时复用当前终端；后台和看门狗运行时不创建窗口。自动化脚本使用 `--output` 将结果写入 UTF-8 文件：

```powershell
.\WuwaTappoPlus.exe --output status.txt status
.\WuwaTappoPlus.exe --output doctor.txt doctor
.\WuwaTappoPlus.exe --output logs.txt logs 80
```

对外命令保持 `install`、`uninstall`、`status`、`doctor`、`logs`，`help` 查看帮助。`doctor --policy` 只读检查策略；`doctor --clean-policy --yes` 仅在管理员权限下显式清理残留。源码运行时，将 EXE 换成 `python launch.py`。

`run`、`ensure`、`menu` 是内部入口。日常操作无需使用这些入口。

## 配置文件

`config.toml` 首次运行时生成，本机安装路径等设置不纳入 Git。常用配置如下，完整默认值见 [config.py](src/wuwa_tappo_plus/config.py)：

```toml
[app]
exe = ""                  # 留空自动寻找 WuwaTappo.exe
host = "127.0.0.1"
port = 9222

[inject]
enabled = true             # 自动开箱
min_balance = 1000          # 低于此余额不点击

[alerts]
low = 2000                 # 余额偏低阈值
critical = 1000            # 余额告急阈值
bubble = true
animation = true

[panel]
keep_open = true

[rooms]
history = 20               # 房间历史保留条数

[daemon]
watchdog_min = 1            # 看门狗检查间隔，单位分钟；0 为关闭
```

四个功能开关手动修改后也会自动加载。修改端口、阈值、轮询间隔或看门狗配置后，执行 `install` 重新安装／修复。

更换调试端口时，旧策略可能产生冲突：先执行 `uninstall` 清理本项目的旧策略，再执行 `install`。安装遇到冲突会中止，不会覆盖未知配置；其他策略条目默认只报告，需要明确执行清理命令才会删除。

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| 设置页没有 Plus 分组，或提示后台未连接 | 运行 `doctor`，必要时重新安装／修复；原应用升级后可能需要适配 |
| 宝箱到期但不自动开启 | 检查自动开箱开关、字符余额和 `min_balance`，再查看日志 |
| 气泡没有出现 | 检查气泡开关；仅在余额低于提醒阈值时显示 |
| 昨日统计显示 `—` | 昨天没有输入计数采样，不能从累计值倒推出每日次数 |
| 调试端口未响应 | 重新安装／修复，让桌宠在策略写入后重新启动 |
| 提示策略或端口冲突 | 用 `doctor --policy` 检查，按提示更换端口或清理冲突 |
| 后台被结束后再次出现 | 看门狗会恢复意外退出的后台；要完整移除请执行卸载 |

日志位于 `logs/auto.log`，按大小轮转。连接中断后会重试；设置页和主桌宠页面重建后会重新注入。

注意：**直接结束 Plus 后台不保证页面里的自动点击停止**，心跳超时仅用于诊断。暂停点击请使用桌宠设置开关；完整清理请使用卸载。

## 工作方式与本地数据

Python 后台通过本机 CDP 连接桌宠的 WebView2，注入自动点击与提醒脚本，并连接独立设置窗口同步开关和图表。自启使用当前用户的注册表启动项，看门狗使用 `\WuwaTappo\KeepAlive` 计划任务。

调试策略仅针对 `WuwaTappo.exe`，默认使用回环地址和端口 `9222`。调试端口允许本机进程访问页面内容，应保留回环地址配置；卸载会移除本项目的策略。

| 路径 | 内容 |
| --- | --- |
| `config.toml` | 本机配置与功能开关 |
| `reports/input.sqlite3` | 每日键鼠输入统计及累计值基线 |
| `reports/rooms.sqlite3` | 房间历史：房间号、房主、进入次数 |
| `logs/` | 轮转日志 |
| `.state.json` | 后台发布的运行状态 |
| `.daemon.lock`、`.disabled` | 单实例锁和停用标记 |

这些运行时数据均不纳入 Git。项目不会修改 Wuwa Tappo 原程序文件；相关操作通过其页面和原生窗口接口完成。

## 开发

项目版本统一定义在 `src/wuwa_tappo_plus/__init__.py` 的 `__version__`，采用 `主版本.次版本.修订号` 格式。后台、主注入脚本、设置面板和房间历史共用它；设置页、安装管理和命令行会显示当前版本。修改任一页面脚本发布新版本时，提升这一处版本号并重启 Plus 后台，旧脚本会自动替换。

版本号由程序管理，无需写入 `config.toml`。

```text
src/wuwa_tappo_plus/  Python 包
  entry.py / console.py 入口分发与按需终端窗口
  paths.py           程序、资源与用户数据路径
  cli.py / menu.py   命令与源码版控制台面板
  runner.py          后台生命周期与状态发布
  cdp.py / ws.py     本机 CDP 与 WebSocket
  win.py             策略、自启动、计划任务和进程管理
  config.py          配置加载和原子保存
  settings.py / rooms.py / reports.py  设置桥接、房间历史、输入统计
  scripts/           三个注入的 JavaScript 模板
tests/               Python 与 JavaScript 回归测试
packaging/           PyInstaller 配置与 Windows manifest
tools/               测试、构建与发布包验证脚本
launch.py            当前启动器
```

开发环境和测试：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[build]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q src tests tools launch.py
node --test tests/test_bubble.js tests/test_panel.js tests/test_rooms.js
```

不安装开发依赖也可运行 `python tools/run_tests.py`。运行项目本身仅依赖 Python 标准库；构建 EXE 才需要 PyInstaller。

在 Windows x64、官方 CPython 环境构建：

```powershell
.\.venv\Scripts\python.exe tools/build_release.py
.\.venv\Scripts\python.exe tools/smoke_release.py
```

构建会检查解释器架构，并自动验证生成的 EXE 能启动、资源和版本完整，再生成源码包与校验文件。输出位于 `dist/`：单文件 `WuwaTappoPlus.exe`、单独的对应源码 ZIP，以及 `SHA256SUMS.txt`。用户运行只需要 EXE；GPLv3 发布时仍应另行提供对应源码与校验文件；不要包含个人配置、数据库、日志或开发运行时。Python、PyInstaller 和项目的许可证均内嵌在 EXE 中。GitHub Actions 可运行测试和手动构建，本仓库未自动发布远程 Release。

测试使用模拟对象和临时目录，避免真实安装、注册表修改或桌宠点击。发布包冒烟测试使用隔离数据目录与未连接的本机端口；安装／卸载的真实系统修改仍应在 Windows 测试环境验证。贡献约定见 [AGENTS.md](AGENTS.md)。

## 许可证

本项目使用 **GPL-3.0-only**，完整文本见 [LICENSE](LICENSE)。原版 Wuwa Tappo 不包含在本项目中；打包组件保留各自许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
