# 仓库贡献指南（Repository Guidelines）

## 项目结构与模块职责

Wuwa Tappo Plus 是 Windows 工具，使用 Python 标准库和注入页面的 JavaScript 脚本。EXE 发布包以 `WuwaTappoPlus.exe` 为用户入口，双击打开原来的数字终端面板。源码唯一启动入口为 `launch.py`。`entry.py` 统一分发源码与冻结程序，`console.py` 仅为前台操作创建或复用终端；`paths.py` 分离程序、资源和数据目录。

- `src/wuwa_tappo_plus/cli.py`、`src/wuwa_tappo_plus/menu.py`：命令分发与控制台面板。
- `src/wuwa_tappo_plus/runner.py`：守护进程生命周期、心跳与状态发布。
- `src/wuwa_tappo_plus/cdp.py`、`src/wuwa_tappo_plus/ws.py`：WebView2 CDP 通信与 WebSocket 传输。
- `src/wuwa_tappo_plus/win.py`：Windows 注册表策略、进程、自启动与计划任务。
- `src/wuwa_tappo_plus/scripts/inject.js`：页面检查、按钮点击与提醒气泡。
- `src/wuwa_tappo_plus/config.py`：默认配置与 TOML 加载；`config.toml`：本地配置。
- `src/wuwa_tappo_plus/settings.py`、`src/wuwa_tappo_plus/scripts/settings.js`：独立设置窗口中的 Plus 开关、配置持久化与运行状态同步。设置脚本同时在面板页挂载图鉴预览：未获得的角色、配件、表情点击弹窗查看；角色用外观目录的 `body1..3`/`head1..2` 帧做敲击与眨眼动画，配件叠加到当前角色上，表情原样展示。外观目录从 IndexedDB 的 `wuwatappo_appearance_asset` 读取，图鉴图标通过 `height_light_id` 映射到真实外观条目，不额外请求服务器。
- `src/wuwa_tappo_plus/rooms.py`、`src/wuwa_tappo_plus/scripts/rooms.js`：联机页的房间历史。页面自己解析房间号、渲染列表并完成回跳（填入真实输入框后点击「加入房间」），Python 只负责落库与回传列表；数据保存在 `reports/rooms.sqlite3`，默认保留 20 条未收藏房间。收藏（`pinned`）房间置顶显示、永不因数量上限被删除，上限同时由条数控制和 `config.toml` 的 `[rooms] history` 控制；旧库缺少该列时启动补列。房间号必须匹配 `[0-9A-Za-z]{4,16}`，写入前做校验；房主名字晚于房间号渲染时只补名字，不计新的一次进入。
- `src/wuwa_tappo_plus/reports.py`：统计桌宠自身的累计点击数（`cumulativeClickCount`，即设置页显示的「累计点击数」）的每日增量；数据保存在 `reports/input.sqlite3`（文件名沿用历史命名）。首次采样只建基线，开箱、消费和旧开箱日志不能计入；缺失日期与已采样的零次须区分。仅保存计数和账户标识的哈希，不记录按键内容。
- `src/wuwa_tappo_plus/toasts.py`、`src/wuwa_tappo_plus/scripts/toast.js`：在独立的 toast 页面里缓存桌宠气泡的原文与时间，后台逐条写入日志（`TOAST at=… text="…"`），并标注是否紧跟一次自动点击。气泡页是单独 WebView，桌宠页面看不到这些文本。
- `src/wuwa_tappo_plus/screens.py`：记录桌宠主窗口所在的显示器（`screen.json`，以硬件 ID 为主、设备名/位置为辅），桌宠重启后通过派发页面自己的 F8 事件触发应用切换显示器；目标显示器不在桌面布局或数量变化时不做移动、保留记录，用户手动切换则更新记录。

`runtime/` 包含内置 Python 3.13。`tests/` 存放隔离回归测试。日志、本地配置、状态文件、锁文件和运行时二进制文件均由 Git 忽略。

## 构建、测试与开发命令

在仓库根目录使用 PowerShell 执行。源码运行仅依赖 Python 3.11+ 标准库；开发可 `pip install -e ".[build]"`。EXE 使用 Windows x64 官方 CPython 与 PyInstaller 构建。

- `.\runtime\python.exe launch.py help`：查看可用命令。
- `.\runtime\python.exe -m compileall -q src tests tools launch.py`：检查 Python 语法。
- `.\runtime\python.exe tools/run_tests.py`：运行隔离回归测试。
- `node --test tests/test_bubble.js tests/test_panel.js tests/test_rooms.js tests/test_toast.js`：验证气泡定位、原生裁切区域桥接、房间回跳与 toast 缓存；仅开发测试需要 Node.js。
- `.\runtime\python.exe launch.py doctor`：检查解释器、应用、策略、连接与守护进程状态。
- `.\runtime\python.exe launch.py status`、`.\runtime\python.exe launch.py logs 80`：查看当前状态与最近日志。
- `.\runtime\python.exe launch.py install`、`.\runtime\python.exe launch.py uninstall`：安装 / 修复或卸载，保留个人配置与功能开关。
- `.\runtime\python.exe launch.py doctor --policy`：只读查看策略；`--clean-policy` 显式清理残留，需要管理员权限和确认。

对外命令仅保留 `install`、`uninstall`、`status`、`doctor`、`logs`；`run`、`ensure`、`menu` 为内部入口。EXE 双击打开终端安装管理，安装／卸载才提权；日常功能开关放在桌宠设置中。不要添加语义重复的 `start`、`stop`、`restart` 命令。重复安装应修复后台与策略，卸载不能因进程未运行而跳过清理。`uninstall` 保留数据；`uninstall --purge` 经确认后完整卸载，`--yes` 用于自动化。仅在卸载成功且后台已退出后删除已知 Plus 数据文件，保留源码、EXE 和其他文件；完整卸载成功后退出面板，避免重新生成配置。

设置页仅允许修改自动开箱、余额提醒、气泡显示、气泡动画、屏幕记忆、自动发表情和小黑屋模式七个布尔值；由后台原子保存到配置，保存成功后才确认。屏幕记忆关闭时既不记录也不移动。暂停点击不停止后台，界面关闭或重建后必须能重新挂载。

官方 1.0.2 开箱后使用组件内部计时，`userInfo.left_treasure_chest/right_treasure_chest` 可能保持旧值。宝箱状态以左右 `.reward-anchor--left/right` 内的原生按钮及 `.reward-countdown__text` 为准；隐藏的倒计时优先使用本账户已确认开箱的本地缓存；无有效缓存时标记为未知，不用旧字段或余额变化推算。缓存只供提醒，不能作为点击授权。必须按目标槽选择按钮，保留各槽独立的重试预算；脚本卸载后的旧验证回调不得继续报错。CLI 与后台共用主注入的快照。

余额读取统一经主注入的 `readBal`：优先读取 `GlobalState.totalClickCount` 非负安全整数（0 是有效余额）， getter 缺失、异常或无效时再用 DOM 数字兜底；不限制为7位数字，不自行用 userInfo 原始计数相减。自动点击、气泡、CLI 与后台必须共用此读数。

## 代码风格与命名约定

沿用现有代码风格：Python 使用四空格缩进，函数和变量使用 `snake_case`，类名使用 `PascalCase`，常量使用全大写。JavaScript 使用两空格缩进和 `camelCase` 命名。目前未配置格式化或静态检查工具。保持现有模块职责，避免引入不必要的依赖。修改默认配置时，同步更新 `BUILTIN` 与 `DEFAULT_TOML`；项目版本唯一来源是 `src/wuwa_tappo_plus/__init__.py` 的 `__version__`，后台、主注入、设置面板与房间历史必须共用该版本；JavaScript 模板通过 Python 注入版本值，不手写独立脚本版本。版本由程序管理，不写入用户配置。

版本号按**发布**提升，不随每次开发改动递增：同一批未发布的改动共用一个版本，发布时再确定。开发中需要让页面重新注入时，重启桌宠或重新执行 `install` 即可，不要用改版本号来触发替换。

## 测试规范

使用标准库 `unittest`，未设置覆盖率门槛。测试采用 `tests/test_*.py` 命名，模拟注册表、进程与 CDP 操作，避免真实副作用。提交前运行测试和语法检查。修改注入脚本时，在明确授权的 Windows 环境验证余额不足、重复注入和断线重连，注明未验证的场景。新增或删除页面脚本时，同步更新 `tools/smoke_release.py` 的脚本清单与资源检查，否则发布冒烟测试会失败。

## 提交与拉取请求规范

主分支为 `main`；开发使用 `fix/简短描述`、`feat/简短描述` 或 `docs/简短描述` 分支。提交遵循 Conventional Commits：`类型(可选范围): 简短说明`，例如 `fix(cli): 停止时清理页面脚本`。常用类型为 `feat`、`fix`、`docs`、`test`、`refactor`、`chore`；每次提交聚焦一项改动。

拉取请求填写 `.github/pull_request_template.md`，说明问题、行为变化、验证结果和配置影响；有关联问题时使用 `Closes #编号`。界面修改附截图。合并前确保测试通过并完成代码审查；优先使用 squash 合并，标题沿用提交规范。没有远程仓库时，先在本地保持同样的提交与审查习惯。

## 配置与安全

调试连接保持绑定回环地址。保留策略冲突检查，注册表修改仅限本应用对应的值。安装和卸载会修改 Windows 配置，可能需要管理员权限。自动化仅点击真实按钮，不构造奖励接口请求，不修改应用二进制文件。提交补丁时排除个人路径与运行时生成的状态。

`config.toml` 由程序写出的部分**始终是无 BOM 的 UTF-8**（`utf-8` 编码器不会写 BOM）；读取时使用 `utf-8-sig`，只是为了容忍外部编辑器或脚本写入的 BOM。改配置时仍应保存为无 BOM：PowerShell 5.1 的 `Set-Content -Encoding utf8` 会写入 BOM，`tomllib` 会把它判成语法错误，表现为后台日志里的 `settings: Invalid statement (at line 1, column 1)` 或启动失败；此时请用 `[IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))`。

`config.toml` 的结构由 `DEFAULT_TOML` 单一决定：读取配置时 `sync_config()` 会把文件对齐到模板——保留仍在用字段的值与注释，删除已退休的字段（连同其注释块），补齐模板里有而文件里缺的字段，未知段落原样保留；只有内容变化才原子写回。新增或删除配置项时只改 `DEFAULT_TOML` 与 `BUILTIN`，不要另写迁移代码，也不要手改用户的配置文件。

## 打包与发布

`pyproject.toml` 定义 `src/` 包布局与 GPL-3.0-only 许可。`python tools/build_release.py` 仅生成单文件 EXE 和只包含该 EXE 哈希的 SHA-256 校验文件，不额外打包源码；`python tools/smoke_release.py` 验证冻结入口、资源、终端窗口、隔离后台拉起和校验值。Release 只上传 EXE 与校验文件，对应源码使用 GitHub 版本标签自动提供的 ZIP / TAR.GZ。发布时保持标签源码与二进制一致，保留构建文件和第三方许可证；不得打入配置、统计、日志或个人路径。

EXE 数据存放在 `%LOCALAPPDATA%\WuwaTappoPlus`，源码数据保留在仓库根目录。`WUWA_TAPPO_PLUS_DATA_DIR` 仅允许绝对路径，用于隔离测试与显式数据定位。资源从包内 `scripts/` 读取，运行命令通过 `win.command_args` 生成；冻结程序不能再启动 `pythonw.exe` 或源码脚本。不要把用户数据写入临时解包目录；启动独立后台和提权进程时设置 `PYINSTALLER_RESET_ENVIRONMENT=1`，确保前台退出后后台资源仍然可用。这是首次公开发布，不引入历史入口、旧配置迁移和旧自启动名称的兼容代码。

### Agent 的发布职责

当用户要求打版本标签或发布版本时，由 Agent 主动完成以下流程，不要求用户手动运行打包或归档命令：

1. 确认统一版本号、发布提交与版本标签对应，生成该版本的中文 Release Notes（写成 `dist/release-notes.md`，构建时归档）。
2. 完成构建、测试和 EXE 启动验证，核对程序及页面脚本版本一致：`build_release.py --notes` 会跑 `--probe`；再跑 `tools/smoke_release.py` 验证冻结入口、资源、终端菜单、看门狗拉起与超时退出。
3. 发布前必须将本次 EXE、SHA256SUMS.txt 和 release-notes.md 保存到 `dist/history/v<版本号>/`；通过 `python tools/build_release.py --notes dist/release-notes.md` 完成。同时在该归档目录生成 `WuwaTappoPlus-<版本号>-windows-x64.zip`，供用户上传网盘，ZIP 内仅放 EXE、SHA256SUMS.txt 和 release-notes.md，不含源码、配置或个人数据。ZIP 只保留本地，不上传 GitHub Release。普通开发构建不必归档；已有版本归档及 ZIP 不得静默覆盖。
4. 推代码和标签：`git push origin main`，`git tag -a v<版本号> -m "..."`，`git push origin v<版本号>`。标签必须指向发布提交，不要在 Release 页面新建标签。
5. 创建 Release 用 GitHub CLI（`winget install --id GitHub.cli -e`，默认路径 `C:\Program Files\GitHub CLI\gh.exe`）。首次使用先授权：`gh auth login --hostname github.com --git-protocol ssh --web`，在浏览器打开 https://github.com/login/device 输入终端显示的一次性代码，`gh auth status` 显示已登录即可。然后执行：

   ```powershell
   gh release create v<版本号> `
     "dist/history/v<版本号>/WuwaTappoPlus.exe" `
     "dist/history/v<版本号>/SHA256SUMS.txt" `
     --title "Wuwa Tappo Plus <版本号>" `
     --notes-file "dist/history/v<版本号>/release-notes.md" `
     --verify-tag
   ```

   `--verify-tag` 强制使用已推送的标签，标签不存在时直接失败，避免 Release 与标签指向不一致。
6. 发布后回读验证：`gh release view v<版本号> --json tagName,name,isDraft,isPrerelease,assets` 检查状态和附件；再用 `gh release download v<版本号> --dir <临时目录>` 重新下载 EXE，核对 SHA-256 与归档中的 `SHA256SUMS.txt` 一致。
7. `dist/` 已整体被 Git 忽略，不为历史归档增加单独规则，不提交二进制历史。GitHub Release 只附 EXE 和校验文件，源码使用标签自动归档。
8. 用户自行发布或登录权限不足时，Agent 仍须准备并归档上述文件，再提供下载路径和可直接粘贴的 Release Notes。

宝箱时间缓存：主注入捕获原生宝箱点击，通过 Pinia `applyChestClickConsume` 成功动作确认开箱，按左右槽记录成功时间与30分钟冷却到期时间。后台原子保存到数据目录 `chests.json`，注入时恢复；账户切换隔离、过期记录忽略，原生按钮与可见倒计时优先。不得将按钮消失、余额下降或点击尝试记作成功；卸载脚本时移除监听与动作订阅，完整卸载清除缓存。

小黑屋模式默认关闭：只包装原生 incrementLocalClickCount/addLocalClickCount，不改水合、余额消费、原始历史或本地持久化。仅拦截默认命名空间 Socket.IO cr 事件；以本地非成功空回执释放 ack 回调，不能伪造服务端成功或清零待上报点击。房间、心跳、开箱及退出登录请求保持原样；关闭或卸载时恢复包装。未来官方协议变化需要重新验证，不能声称停止所有网络通信。

小黑屋模式开启时常驻显示“库爹我错了，我再也不刷点击数了T T”，独立于余额提醒开关与余额阈值，但受气泡显示总开关控制，优先于临时提醒；关闭模式后恢复正常气泡，卸载时移除。文字气泡只在本地绘制，原生联机敲击动作同步保留。

设置顺序：自动开箱、自动发表情、余额提醒、气泡显示、气泡动画、屏幕记忆、小黑屋模式。气泡显示控制全部 Plus 气泡；余额提醒只控制低余额提醒（含日志），关闭时重置日志层级基线，不伪报余额恢复。

日志导出作为独立操作放在桌宠 Plus 设置中，不添加 EXE 菜单或 CLI 入口。后台仅接受 export_logs 动作，将 ZIP 写入系统桌面；不接受页面指定路径，不导出原始配置、凭据和数据库。日志默认每文件 1 MiB、3 份备份，写入前轮转，单条最多 16 KiB；导出读取历史文件也必须限量，失败删除不完整 ZIP，重复请求不得重复生成。

日志导出同时收集 `%LOCALAPPDATA%\com.kuro.wuwatappo.cn\logs` 下直接存放的 `.log` 和 `.log.数字` 文件，按修改时间取最近最多 8 个，每个读取末尾最多 2 MiB、合计最多 8 MiB。禁止递归收集 WebView、账户数据及链接目录；原生日志缺失或不可读必须在摘要中注明，不阻断 Plus 日志导出。日志原文可能有个人信息，不能声称已脱敏。

自动发表情默认关闭：仅在原生 incrementLocalClickCount 单次敲击动作触发时，从当前 EmotionMenu.props.emoteItems 中随机选一个，通过原生 select 事件发送；不拦截批量加数、水合或开箱，不自行构造表情网络请求。与小黑屋模式共享动作包装，关闭和卸载需恢复原动作，组件或表情缺失时不得影响正常敲击。

Windows 构建前检查内置 runtime/python.exe 不带 Low 完整性标签；项目根目录可能带可继承的 Low 标签，不能据此降低构建解释器或交付 EXE。构建器在启动验证前显式将交付 EXE 设置为 Medium；只有启动验证成功后才生成校验文件。修复时只改相关可执行文件的标签，不递归重置仓库权限。

自动表情采用1000毫秒滑动窗口，最多20次发送尝试；优先用 performance.now() 计时，超额直接丢弃，不排队、不重试，不影响原生计数或敲击动画，切换开关或小黑屋模式不重置限额。此限制只约束 Plus 自动发送，不改变原生手动表情。

房间内「显示他人桌宠」「隐藏他人桌宠」两个独立按钮仅挂载于 .room-module .room-info，使用原生 common-btn 并放在离开房间旁；通过当前 .room-visible-btn 的真实点击批量切换，排除自身和离线成员，不改桌宠 CSS 或构造通信请求。显示按钮只显示当前隐藏者，隐藏按钮只隐藏当前可见者；无对应操作时禁用，等待原生状态更新期间禁止重复操作；离房、页面重建和卸载需移除按钮，空房禁用。

EXE 安装到 %LOCALAPPDATA%\Programs\WuwaTappoPlus，数据目录独立。installation.py 只管理固定 EXE 和 install.json，拒绝链接路径；临时文件原子替换、失败保留旧 EXE。安装后自启动、看门狗及后台工作目录均指向安装目录，UAC 保留原用户程序目录。两种卸载都删除程序文件，源码和下载包不删除；运行中 EXE 用隐藏 PowerShell 清理进程退出后删除，按安装代次和 SHA-256 防止误删重新安装的新程序，禁止递归删除目录。两种卸载成功后退出菜单，完整卸载还清理已知数据。
