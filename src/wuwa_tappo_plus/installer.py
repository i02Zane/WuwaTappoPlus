"""Fixed installer operations; Setup owns files and the original user's HKCU."""

import argparse
import os
import stat
import time
import tomllib
from pathlib import Path

from . import cdp, daemon_control, installation, lifecycle, win
from .config import Config, load
from .paths import DATA_DIR


def validate_paths():
    root = DATA_DIR
    for path in [*root.parents, root]:
        if path.exists() and (path.is_symlink() or path.lstat().st_file_attributes
                              & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise OSError("数据目录不可经过链接。")
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(current) / name
            if path.is_symlink() or path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise OSError("数据目录包含链接，已中止操作：%s" % path)
    installation.validate()


def _stop():
    result = daemon_control.stop()
    if result.exit_code:
        raise OSError(result.steps[-1].message)


def prepare():
    running = win.check_app_permissions(print)
    cfg = load()
    exe = win.find_exe(cfg.exe)
    if exe is None:
        raise OSError("未找到 WuwaTappo.exe，请先安装桌宠或设置 app.exe。")
    candidates, conflicts = lifecycle._choose_policy_ports(cfg.port)
    if conflicts or not candidates:
        raise OSError("WebView2 调试策略冲突，请先运行 doctor --policy 检查。")
    # Emitted only after read-only checks; Setup may resume after this point.
    print("installer: app_was_running=%d" % running)
    _stop()
    if running and win.stop_app(exe) is None:
        raise OSError("无法停止桌宠，请关闭桌宠后重试。")
    try:
        port = next((value for value in candidates if win.port_available(value, cfg.host)), None)
        if port is None:
            raise OSError("候选调试端口均被占用。")
        lifecycle.write_config_exe(exe)
        if port != cfg.port:
            lifecycle.write_config_port(port)
        installation.record_installation(pending=True)
    except Exception:
        if running:
            win.launch_app(exe)
        raise
    return running


def configure(user):
    if not win.is_admin():
        raise OSError("系统配置需要管理员权限。")
    # Read the original user's hints without reconciling their config as admin.
    config = tomllib.loads((DATA_DIR / "config.toml").read_text(encoding="utf-8-sig"))
    cfg = Config(config)
    if not 1 <= cfg.port <= 65535 or cfg.host not in ("127.0.0.1", "localhost"):
        raise ValueError("调试连接必须使用有效的回环地址和端口。")
    if not user:
        raise ValueError("安装器必须明确指定原用户。")
    if win.policy_conflicts(cfg.port):
        raise OSError("调试策略发生变化，未覆盖其他配置。")
    win.write_policy(cfg.port)
    minutes = max(0, int(config.get("daemon", {}).get("watchdog_min", 1)))
    if minutes:
        if not win.register_watchdog(minutes, user=user):
            raise OSError("无法为原用户注册看门狗计划任务，请重新运行安装包修复。")
    else:
        win.remove_watchdog()


def start(setup_source=None, *, restart_app=False):
    cfg = lifecycle.read_config_hints()
    if restart_app:
        if not win.processes():
            exe = win.find_exe(cfg.exe)
            if exe is None:
                raise OSError("未找到桌宠，请重新运行安装包修复。")
            win.launch_app(exe)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if cdp.probe_port(cfg.port):
                break
            time.sleep(1)
        else:
            raise OSError("桌宠调试端口未就绪，请重新运行安装包修复。")
    else:
        print("桌宠原本未运行，保持关闭；手动打开桌宠后 Plus 后台会自动连接。")
    result = daemon_control.start()
    if result.exit_code:
        raise OSError(result.steps[-1].message)
    installation.record_installation(setup_source=setup_source)


def remove_system():
    if not win.is_admin():
        raise OSError("清理系统配置需要管理员权限。")
    result = lifecycle.uninstall(remove_program=False, manage_autostart=False, restart_app=False)
    if result.exit_code:
        raise OSError(result.steps[-1].message)


def detach():
    cfg = lifecycle.read_config_hints()
    if win.processes() and win.restart_app(cfg.exe) is None:
        raise OSError("无法重启桌宠，未清除数据或程序文件。")


def resume(*, restart_app=False):
    """Best-effort recovery when an upgrade was cancelled before file copying."""
    if (installation.directory() / installation.EXE_NAME).is_file():
        if restart_app and not win.processes():
            cfg = lifecycle.read_config_hints()
            exe = win.find_exe(cfg.exe)
            if exe:
                win.launch_app(exe)
        daemon_control.start()


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "configure", "start", "stop",
                                          "remove-system", "detach", "purge", "resume"))
    parser.add_argument("--user")
    parser.add_argument("--setup-source")
    parser.add_argument("--restart-app", action="store_true")
    args = parser.parse_args(argv)
    try:
        validate_paths()
        win._launch_executable = installation.directory() / installation.EXE_NAME
        if args.action == "configure":
            configure(args.user)
        elif args.action == "start":
            start(args.setup_source, restart_app=args.restart_app)
        elif args.action == "prepare":
            prepare()
        elif args.action == "resume":
            resume(restart_app=args.restart_app)
        else:
            actions = {"stop": _stop,
                       "remove-system": remove_system, "detach": detach,
                       "purge": lifecycle.purge_data}
            actions[args.action]()
        print("installer: %s completed" % args.action)
        return 0
    except (OSError, ValueError) as exc:
        print("installer: %s failed: %s" % (args.action, exc))
        return 1
