"""Install, repair and uninstall orchestration without terminal interaction."""

import json
import os
import re
import stat
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path

from . import accessories, cdp, daemon_control, installation, win
from .config import CONFIG_PATH, Config
from .operations import Progress
from .paths import DATA_DIR


PORT_ATTEMPTS = 5


def _choose_policy_ports(base):
    """Policy-free candidate ports, starting at the configured one."""
    ports = []
    for port in range(base, base + PORT_ATTEMPTS):
        conflicts = win.policy_conflicts(port)
        values = [c for c in conflicts if c["kind"] == "value"]
        if values:
            return ports, values
        if any(c["kind"] == "port" for c in conflicts):
            continue
        ports.append(port)
    return ports, []


def _write_app_value(key, value):
    """Change only the requested app value, atomically and without a BOM."""
    text = CONFIG_PATH.read_text(encoding="utf-8-sig")
    parsed = tomllib.loads(text)
    if parsed.get("app", {}).get(key) == value:
        return
    if type(value) is not int and ("\n" in str(value) or "\r" in str(value)):
        raise ValueError("程序路径无法保存为 TOML 字符串。")
    # JSON basic strings are also TOML strings for Windows executable paths.
    literal = str(value) if type(value) is int else json.dumps(str(value), ensure_ascii=False)
    line = "%s = %s" % (key, literal)
    section = re.search(r"(?m)^\[app\][ \t]*(?:#.*)?$", text)
    if section is None:
        raise ValueError("配置缺少 [app] 段，请重新执行安装 / 修复。")
    next_section = re.search(r"(?m)^\[", text[section.end():])
    end = section.end() + next_section.start() if next_section else len(text)
    body = text[section.end():end]
    pattern = r"(?m)^" + re.escape(key) + r"\s*=.*$"
    if re.search(pattern, body):
        body = re.sub(pattern, lambda _match: line, body, count=1)
    else:
        body = "\n" + line + body
    updated = text[:section.end()] + body + text[end:]
    # Validate before replacing the user's configuration.
    tomllib.loads(updated)
    fd, name = tempfile.mkstemp(prefix=".plus-config-", suffix=".tmp", dir=CONFIG_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(updated)
        os.replace(name, CONFIG_PATH)
    finally:
        Path(name).unlink(missing_ok=True)


def write_config_exe(exe):
    _write_app_value("exe", str(exe))


def write_config_port(port):
    _write_app_value("port", port)


def read_config_hints(progress=None):
    """Read only the two uninstall hints; never create or reconcile config."""
    progress = progress or Progress()
    progress.stage = "read_config"
    try:
        data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        app = data.get("app", {})
        exe = app.get("exe", "")
        port = app.get("port", 9222)
        if not isinstance(exe, str) or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("app.exe 或 app.port 无效")
        return Config({"app": {"exe": exe, "port": port}})
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        progress.add("warning", "无法读取卸载配置，使用默认定位信息，原文件保持不变：%s" % exc)
        return Config({})


def install(cfg, callback=None):
    """Repair an installation, stopping at the first failed dependency."""
    progress = Progress(callback)
    try:
        progress.stage = "find_app"
        exe = win.find_exe(cfg.exe)
        if not exe:
            return progress.add("failed", "未找到 WuwaTappo.exe，请在 %s 中设置 app.exe 路径后重试。" % CONFIG_PATH)
        progress.add("completed", "桌宠程序：%s" % exe)

        progress.stage = "check_policy"
        ports, conflicts = _choose_policy_ports(cfg.port)
        if conflicts:
            details = "; ".join("%s\\%s = %s" % (c["hive"], c["name"], c["value"]) for c in conflicts)
            return progress.add("failed", "检测到策略冲突，未更改安装设置：%s" % details)
        if not ports:
            return progress.add("failed", "候选调试端口都被其他策略占用，请执行 doctor --policy 检查。")
        progress.add("completed", "调试策略冲突检查通过。")

        if not progress.include(daemon_control.stop(callback)):
            return progress.result

        progress.stage = "stop_app"
        app_running = bool(win.processes())
        if app_running:
            if win.stop_app(exe) is None:
                return progress.add("failed", "未找到 WuwaTappo.exe，无法重启桌宠。")
            progress.add("completed", "桌宠已停止，开始检查调试端口。")
        else:
            progress.add("skipped", "桌宠未运行，无需停止。")

        progress.stage = "select_port"
        port = next((p for p in ports if win.port_available(p, cfg.host)), None)
        if port is None:
            if app_running:
                win.launch_app(exe)
            return progress.add("failed", "候选调试端口都被占用：%s" % ", ".join(map(str, ports)))
        if port != cfg.port:
            write_config_port(port)
        progress.add("completed", "使用调试端口：%d。" % port)

        progress.stage = "deploy_program"
        installed = installation.deploy()
        if installed:
            win._launch_executable = installed
            progress.add("completed", "Plus 程序目录：%s" % installed.parent)
        else:
            progress.add("skipped", "源码运行，无需复制程序文件。")

        progress.stage = "save_app_path"
        write_config_exe(exe)
        progress.add("completed", "桌宠路径已保存。")
        progress.stage = "write_policy"
        args = win.write_policy(port)
        progress.add("completed", "调试策略：%s = %s" % (win.TARGET_EXE, args))
        progress.stage = "launch_app"
        win.launch_app(exe)
        progress.add("completed", "桌宠程序已启动。")

        progress.stage = "wait_debug_port"
        deadline = time.monotonic() + 25
        version = None
        while time.monotonic() < deadline:
            version = cdp.probe_port(port)
            if version:
                break
            time.sleep(1.0)
        if not version:
            return progress.add("failed", "等待调试端口 %d 超时，请重新执行安装 / 修复。" % port)
        progress.add("completed", "调试端口已就绪：%d（%s）" % (port, version.get("Browser")))

        progress.stage = "register_autostart"
        command = win.enable_autostart()
        progress.add("completed", "开机自启已配置：%s" % command)
        progress.stage = "register_watchdog"
        if cfg.watchdog_min > 0:
            if win.register_watchdog(cfg.watchdog_min):
                progress.add("completed", "后台守护任务已配置：每 %d 分钟检查。" % cfg.watchdog_min)
            else:
                progress.add("warning", "无法创建后台守护计划任务，请重新执行安装 / 修复。")
        elif win.remove_watchdog():
            progress.add("completed", "config.toml 中已关闭后台守护任务，旧任务已移除。")
        else:
            progress.add("skipped", "config.toml 中已关闭后台守护任务。")

        progress.include(daemon_control.start(callback))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        progress.add("failed", "步骤 %s 失败：%s。已完成的步骤保留，请重新安装 / 修复。" % (progress.stage, exc))
    return progress.result


def uninstall(purge=False, callback=None, *, remove_program=True, manage_autostart=True,
              restart_app=True):
    """Remove owned setup before touching personal data or program files."""
    progress = Progress(callback)
    try:
        cfg = read_config_hints(progress)
        progress.stage = "find_app"
        app_running = restart_app and bool(win.processes())
        if not progress.include(daemon_control.stop(callback)):
            return progress.result

        progress.stage = "remove_policy"
        removed = win.remove_policy()
        progress.add("completed" if removed else "warning", "调试策略已移除。" if removed else "调试策略不存在，或暂时无法移除。")
        for label, _hive, name, value in win.stray_policy_values(cfg.port):
            progress.add("skipped", "其他策略保留不变：%s\\%s = %s（不属于本工具）" % (label, name, value))
        if manage_autostart:
            progress.stage = "remove_autostart"
            removed = win.disable_autostart()
            progress.add("completed" if removed else "skipped", "开机自启已移除。" if removed else "未注册开机自启。")
        progress.stage = "remove_watchdog"
        removed = win.remove_watchdog()
        progress.add("completed" if removed else "warning", "后台守护任务已移除。" if removed else "后台守护任务不存在，或暂时无法移除。")
        progress.stage = "verify_cleanup"
        if (win.read_policy().get(win.TARGET_EXE)
                or (manage_autostart and win.read_autostart()) or win.read_watchdog()):
            return progress.add("failed", "卸载清理未完成，请检查调试策略、开机自启和后台守护任务。")
        progress.add("completed", "系统设置清理已确认。")

        progress.stage = "restart_app"
        if app_running:
            if win.restart_app(cfg.exe) is None:
                return progress.add("failed", "未找到 WuwaTappo.exe，请手动关闭桌宠后重试卸载，数据和程序文件保留。")
            progress.add("completed", "桌宠已重启，注入脚本已清除。")
        elif restart_app:
            progress.add("skipped", "桌宠未运行，无需重启。")

        progress.stage = "verify_daemon_exit"
        if daemon_control.status()[1] or daemon_control.lock_held():
            return progress.add("failed", "后台进程或实例锁仍未释放，保留数据和程序文件。")
        if purge:
            progress.stage = "purge_data"
            purge_data()
            progress.add("completed", "Plus 个人数据已清除。")
        if not remove_program:
            return progress.result
        progress.stage = "remove_program"
        removed = installation.remove_program()
        if removed == "deferred":
            progress.add("completed", "退出窗口后将自动删除安装目录中的程序文件。")
        elif removed == "removed":
            progress.add("completed", "安装目录中的程序文件已删除。")
        else:
            progress.add("skipped", "源码运行，保留源码和下载包。")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        progress.add("failed", "步骤 %s 失败：%s。已完成的清理保留，请重试卸载。" % (progress.stage, exc))
    return progress.result


def purge_data():
    """Remove only known Plus data, even when data lives in the checkout."""
    if daemon_control.status()[1] or daemon_control.lock_held():
        raise OSError("Plus 后台仍在运行，未清除数据。")
    root = DATA_DIR.resolve()
    files = [root / name for name in
             ("config.toml", ".state.json", ".state.tmp", ".daemon.lock", ".stop", ".disabled",
              "screen.json", "chests.json", "chests.tmp", "plaza_auth.json", "plaza_auth.tmp", accessories.FILE_NAME)]
    files.extend(root.glob(".plus-config-*.tmp"))
    files.extend(root.glob(".plus-accessories-*.tmp"))
    for database in ("input.sqlite3", "rooms.sqlite3"):
        files.extend(root / "reports" / (database + suffix)
                     for suffix in ("", "-wal", "-shm", "-journal"))
    files.append(root / "logs" / "auto.log")
    files.extend(path for path in (root / "logs").glob("auto.log.*")
                 if path.suffix[1:].isdigit())
    # Validate every target before deleting anything; do not follow links out.
    for path in [root / "logs", root / "reports", *files]:
        reparse = (path.exists() and getattr(path.lstat(), "st_file_attributes", 0)
                   & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        if not path.resolve().is_relative_to(root) or path.is_symlink() or reparse:
            raise OSError("为避免误删，不清理链接指向的数据路径：%s" % path)
        if path in files and path.exists() and not path.is_file():
            raise OSError("数据路径不是普通文件，已中止清理：%s" % path)
    for path in files:
        path.unlink(missing_ok=True)
    for directory in (root / "logs", root / "reports", root):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
