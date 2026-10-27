# 仓库贡献指南（Repository Guidelines）

## 项目结构与模块职责

Wuwa Tappo Plus 是 Windows 工具，使用 Python 标准库和注入页面的 JavaScript 脚本。EXE 发布包以 `WuwaTappoPlus.exe` 为用户入口，双击打开原来的数字终端面板。源码唯一启动入口为 `launch.py`。`entry.py` 统一分发源码与冻结程序，`console.py` 仅为前台操作创建或复用终端；`paths.py` 分离程序、资源和数据目录。

- `src/wuwa_tappo_plus/cli.py`、`src/wuwa_tappo_plus/menu.py`：命令分发与控制台面板。
- `src/wuwa_tappo_plus/runner.py`：守护进程生命周期、心跳与状态发布。
- `src/wuwa_tappo_plus/cdp.py`、`src/wuwa_tappo_plus/ws.py`：WebView2 CDP 通信与 WebSocket 传输。
- `src/wuwa_tappo_plus/win.py`：Windows 注册表策略、进程、自启动与计划任务。
- `src/wuwa_tappo_plus/scripts/inject.js`：页面检查、按钮点击与提醒气泡。
- `src/wuwa_tappo_plus/config.py`：默认配置与 TOML 加载；`config.toml`：本地配置。
- `src/wuwa_tappo_plus/settings.py`、`src/wuwa_tappo_plus/scripts/settings.js`：独立设置窗口中的 Plus 开关、配置持久化与运行状态同步。
- `src/wuwa_tappo_plus/rooms.py`、`src/wuwa_tappo_plus/scripts/rooms.js`：联机页的房间历史。页面自己解析房间号、渲染列表并完成回跳（填入真实输入框后点击「加入房间」），Python 只负责落库与回传列表；数据保存在 `reports/rooms.sqlite3`，默认保留 20 条。房间号必须匹配 `[0-9A-Za-z]{4,16}`，写入前做校验；房主名字晚于房间号渲染时只补名字，不计新的一次进入。
- `src/wuwa_tappo_plus/reports.py`：使用桌宠原生累计输入计数统计每日键鼠合计增量；数据保存在 `reports/input.sqlite3`。首次采样只建基线，开箱、消费和旧开箱日志不能计入；缺失日期与已采样的零次须区分。仅保存计数和账户标识的哈希，不记录按键内容。

`runtime/` 包含内置 Python 3.13。`tests/` 存放隔离回归测试。日志、本地配置、状态文件、锁文件和运行时二进制文件均由 Git 忽略。

## 构建、测试与开发命令

在仓库根目录使用 PowerShell 执行。源码运行仅依赖 Python 3.11+ 标准库；开发可 `pip install -e ".[build]"`。EXE 使用 Windows x64 官方 CPython 与 PyInstaller 构建。

- `.\runtime\python.exe launch.py help`：查看可用命令。
- `.\runtime\python.exe -m compileall -q src tests tools launch.py`：检查 Python 语法。
- `.\runtime\python.exe tools/run_tests.py`：运行隔离回归测试。
- `node --test tests/test_bubble.js tests/test_panel.js tests/test_rooms.js`：验证气泡定位、原生裁切区域桥接与房间回跳；仅开发测试需要 Node.js。
- `.\runtime\python.exe launch.py doctor`：检查解释器、应用、策略、连接与守护进程状态。
- `.\runtime\python.exe launch.py status`、`.\runtime\python.exe launch.py logs 80`：查看当前状态与最近日志。
- `.\runtime\python.exe launch.py install`、`.\runtime\python.exe launch.py uninstall`：安装 / 修复或卸载，保留个人配置与功能开关。
- `.\runtime\python.exe launch.py doctor --policy`：只读查看策略；`--clean-policy` 显式清理残留，需要管理员权限和确认。

对外命令仅保留 `install`、`uninstall`、`status`、`doctor`、`logs`；`run`、`ensure`、`menu` 为内部入口。EXE 双击打开终端安装管理，安装／卸载才提权；日常功能开关放在桌宠设置中。不要添加语义重复的 `start`、`stop`、`restart` 命令。重复安装应修复后台与策略，卸载不能因进程未运行而跳过清理。`uninstall` 保留数据；`uninstall --purge` 经确认后完整卸载，`--yes` 用于自动化。仅在卸载成功且后台已退出后删除已知 Plus 数据文件，保留源码、EXE 和其他文件；完整卸载成功后退出面板，避免重新生成配置。

设置页仅允许修改自动开箱、气泡显示、气泡动画和主窗口保持打开四个布尔值；由后台原子保存到配置，保存成功后才确认。保持打开使用原生外部点击控制接口，保留手动关闭，卸载时恢复原行为。暂停点击不停止后台，界面关闭或重建后必须能重新挂载。

## 代码风格与命名约定

沿用现有代码风格：Python 使用四空格缩进，函数和变量使用 `snake_case`，类名使用 `PascalCase`，常量使用全大写。JavaScript 使用两空格缩进和 `camelCase` 命名。目前未配置格式化或静态检查工具。保持现有模块职责，避免引入不必要的依赖。修改默认配置时，同步更新 `BUILTIN` 与 `DEFAULT_TOML`；项目版本唯一来源是 `src/wuwa_tappo_plus/__init__.py` 的 `__version__`，后台、主注入、设置面板与房间历史必须共用该版本；JavaScript 模板通过 Python 注入版本值，不手写独立脚本版本。修改任一页面脚本发布新版本时必须提升项目版本，重启 Plus 后台后自动替换旧脚本。版本由程序管理，不写入用户配置。

## 测试规范

使用标准库 `unittest`，未设置覆盖率门槛。测试采用 `tests/test_*.py` 命名，模拟注册表、进程与 CDP 操作，避免真实副作用。提交前运行测试和语法检查。修改注入脚本时，在明确授权的 Windows 环境验证余额不足、重复注入和断线重连，注明未验证的场景。

## 提交与拉取请求规范

主分支为 `main`；开发使用 `fix/简短描述`、`feat/简短描述` 或 `docs/简短描述` 分支。提交遵循 Conventional Commits：`类型(可选范围): 简短说明`，例如 `fix(cli): 停止时清理页面脚本`。常用类型为 `feat`、`fix`、`docs`、`test`、`refactor`、`chore`；每次提交聚焦一项改动。

拉取请求填写 `.github/pull_request_template.md`，说明问题、行为变化、验证结果和配置影响；有关联问题时使用 `Closes #编号`。界面修改附截图。合并前确保测试通过并完成代码审查；优先使用 squash 合并，标题沿用提交规范。没有远程仓库时，先在本地保持同样的提交与审查习惯。

## 配置与安全

调试连接保持绑定回环地址。保留策略冲突检查，注册表修改仅限本应用对应的值。安装和卸载会修改 Windows 配置，可能需要管理员权限。自动化仅点击真实按钮，不构造奖励接口请求，不修改应用二进制文件。提交补丁时排除个人路径与运行时生成的状态。

## 打包与发布

`pyproject.toml` 定义 `src/` 包布局与 GPL-3.0-only 许可。`python tools/build_release.py` 生成单文件 EXE、单独的对应源码 ZIP 和 SHA-256 校验文件；`python tools/smoke_release.py` 验证冻结入口、资源、终端窗口和隔离后台拉起。发布时保持源码与二进制版本一致，保留第三方许可证；不得打入配置、统计、日志或个人路径。

EXE 数据存放在 `%LOCALAPPDATA%\WuwaTappoPlus`，源码数据保留在仓库根目录。`WUWA_TAPPO_PLUS_DATA_DIR` 仅允许绝对路径，用于隔离测试与显式数据定位。资源从包内 `scripts/` 读取，运行命令通过 `win.command_args` 生成；冻结程序不能再启动 `pythonw.exe` 或源码脚本。不要把用户数据写入临时解包目录；启动独立后台和提权进程时设置 `PYINSTALLER_RESET_ENVIRONMENT=1`，确保前台退出后后台资源仍然可用。这是首次公开发布，不引入历史入口、旧配置迁移和旧自启动名称的兼容代码。
