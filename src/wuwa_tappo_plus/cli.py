"""Command line interface."""

import json
import stat
import subprocess
import sys
import time
from datetime import datetime

from . import __version__, cdp, win, installation
from .config import (
    CONFIG_PATH,
    DISABLED_PATH,
    LOG_DIR,
    STATE_PATH,
    load,
)
from .logs import Log
from .paths import DATA_DIR

OK = "[完成] "
BAD = "[失败] "
WARN = "[提示] "


def out(text=""):
    print(text, flush=True)


def fmt_remain(ms_remain):
    if ms_remain is None:
        return "-"
    if ms_remain <= 0:
        return "ready"
    sec = int(ms_remain // 1000)
    if sec >= 3600:
        return "%dh %02dm" % (sec // 3600, (sec % 3600) // 60)
    if sec >= 60:
        return "%dm %02ds" % (sec // 60, sec % 60)
    return "%ds" % sec


def read_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def daemon_status():
    state = read_state()
    pid = state.get("pid")
    alive = bool(pid) and win.pid_alive(pid)
    return state, pid if alive else None


def _arm():
    """Clear the 'stopped on purpose' marker so the watchdog may revive us."""
    try:
        DISABLED_PATH.unlink()
    except OSError:
        pass


def _disarm():
    """Mark a deliberate stop. Without it the watchdog would fight the user."""
    try:
        DISABLED_PATH.write_text("stopped by user\n", encoding="utf-8")
    except OSError:
        pass


def _lock_held():
    """True when a daemon already owns the single-instance lock.

    state.json is written a moment after the lock is taken, so right after a
    start it can still show the previous, dead pid. Probing the lock itself is
    the only race-free answer to 'is somebody already there'.
    """
    from .config import LOCK_PATH

    if not LOCK_PATH.exists():
        return False
    try:
        import msvcrt
    except ImportError:
        return False
    try:
        handle = open(LOCK_PATH, "a+")
    except OSError:
        return True  # open() itself fails with a sharing violation
    try:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return True
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return False
    finally:
        try:
            handle.close()
        except OSError:
            pass


# --------------------------------------------------------------- live probe


def live_state(cfg):
    """Read the countdown straight from the running app. None if unreachable."""
    session = cdp.Session(cfg.port, cfg.host, timeout=8.0)
    try:
        session.connect()
        return session.state(), session.target.get("url")
    except (cdp.CDPError, OSError):
        return None, None
    finally:
        session.close()


# ------------------------------------------------------------------ install


def cmd_install(argv):
    """install or repair Plus; preserve feature switches"""
    cfg = load()
    elevated = "--elevated" in argv

    if not win.is_admin():
        if elevated:
            out(BAD + "未获得管理员权限，安装已中止。")
            return 1
        out("写入 WebView2 策略需要管理员权限。")
        out("正在请求管理员权限，请确认系统弹窗...")
        if win.relaunch_elevated(["install", "--elevated"]):
            return 0
        out("未获得管理员授权，未更改安装设置。")
        return 1

    out("Wuwa Tappo Plus v%s - 安装 / 修复" % __version__)
    out("")
    out(OK + "运行环境：%s" % win.interpreter_label())

    exe = win.find_exe(cfg.exe)
    if not exe:
        out(BAD + "未找到 WuwaTappo.exe。")
        out("       请在 %s 中设置 app.exe 路径后重试。" % CONFIG_PATH)
        return 1
    out(OK + "桌宠程序：%s" % exe)

    conflicts = win.policy_conflicts(cfg.port)
    if conflicts:
        out(BAD + "检测到策略冲突，未更改安装设置。")
        out("")
        for c in conflicts:
            if c["kind"] == "value":
                out("       %s\\%s 已设置为 %s"
                    % (c["hive"], c["name"], c["value"]))
                out("       本工具需要：%s" % win.debug_args(cfg.port))
                out("       该配置并非本工具预期的值，为避免破坏现有设置，不会覆盖。")
            else:
                out("       %s\\%s 也使用端口 %d：%s"
                    % (c["hive"], c["name"], cfg.port, c["value"]))
                out("       两个应用不能共用该端口，否则可能连接到错误的应用。")
        out("")
        out("  可以更换调试端口：")
        out("      在 %s 中设置 port = %d" % (CONFIG_PATH, cfg.port + 1))
        out("      然后重新执行 WuwaTappoPlus.exe install")
        if any(c["kind"] == "value" for c in conflicts):
            out("")
            out("  确认旧配置已不再使用时，也可执行卸载清理：")
            out("      WuwaTappoPlus.exe uninstall（仅移除 %s 对应的策略值）" % win.TARGET_EXE)
        if any(c["kind"] == "port" for c in conflicts):
            out("")
            out("  或检查并释放冲突端口：")
            out("      WuwaTappoPlus.exe doctor --policy         只读检查策略")
            out("      WuwaTappoPlus.exe doctor --clean-policy 确认后清理残留策略")
        return 1

    rc = _stop_daemon([])
    if rc:
        return rc

    try:
        installed = installation.deploy()
    except (OSError, subprocess.SubprocessError) as exc:
        out(BAD + "程序安装失败：%s" % exc)
        return 1
    if installed:
        win._launch_executable = installed
        out(OK + "Plus 程序目录：%s" % installed.parent)

    _write_config_exe(exe)
    args = win.write_policy(cfg.port)
    out(OK + "调试策略：%s = %s" % (win.TARGET_EXE, args))

    try:
        if win.processes():
            out("       正在重启桌宠，以加载策略和页面配置...")
            if win.restart_app(exe) is None:
                out(BAD + "未找到 WuwaTappo.exe，无法重启桌宠。")
                return 1
        else:
            win.launch_app(exe)
        out(OK + "桌宠程序已启动。")
    except OSError as exc:
        out(BAD + "桌宠启动或重启失败：%s" % exc)
        return 1

    out("       正在等待调试端口就绪...")
    deadline = time.time() + 25
    version = None
    while time.time() < deadline:
        version = cdp.probe_port(cfg.port)
        if version:
            break
        time.sleep(1.0)

    if version:
        out(OK + "调试端口已就绪：%d（%s）" % (cfg.port, version.get("Browser")))
    else:
        out(BAD + "等待调试端口 %d 超时。" % cfg.port)
        out("       桌宠可能需要完全重启，请重新执行：WuwaTappoPlus.exe install")
        _finish_elevated(elevated)
        return 1

    cmd = win.enable_autostart()
    out(OK + "开机自启已配置：%s" % cmd)

    minutes = cfg.watchdog_min
    if minutes > 0:
        if win.register_watchdog(minutes):
            out(OK + "后台守护任务已配置：每 %d 分钟检查并恢复意外退出的后台。"
                % minutes)
        else:
            out(WARN + "无法创建后台守护计划任务。")
            out("       请以管理员身份重新执行安装 / 修复。")
    elif win.remove_watchdog():
        out(WARN + "config.toml 中已关闭后台守护任务。")

    rc = _start_daemon([])
    out("")
    if rc == 0:
        out("安装完成。请打开桌宠的「设置 → Wuwa Tappo Plus」调整功能开关。")
    else:
        out("安装未完成，请根据上方提示排查后重新执行安装 / 修复。")
    _finish_elevated(elevated)
    return rc


def _finish_elevated(elevated):
    if elevated and sys.stdin and sys.stdin.isatty():
        try:
            input("\n按 Enter 键关闭此窗口...")
        except EOFError:
            pass


def _write_config_exe(exe):
    """Store the discovered path as a TOML literal string (no backslash escaping)."""
    import re

    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
    except OSError:
        return
    new_line = "exe = '%s'" % str(exe)
    if re.search(r"(?m)^exe\s*=", text):
        # lambda keeps backslashes in the Windows path literal.
        text = re.sub(r"(?m)^exe\s*=.*$", lambda _m: new_line, text, count=1)
    else:
        text = text.replace("[app]\n", "[app]\n" + new_line + "\n", 1)
    try:
        CONFIG_PATH.write_text(text, encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- uninstall


def _purge_data():
    """Remove only known Plus data, even when data lives in the checkout."""
    if daemon_status()[1] or _lock_held():
        raise OSError("Plus 后台仍在运行，未清除数据。")
    root = DATA_DIR.resolve()
    files = [root / name for name in
             ("config.toml", ".state.json", ".state.tmp", ".daemon.lock", ".stop", ".disabled",
              "screen.json", "chests.json", "chests.tmp")]
    files.extend(root.glob(".plus-config-*.tmp"))
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


def cmd_uninstall(argv):
    """uninstall (keep data); --purge also deletes personal data after confirmation"""
    purge = "--purge" in argv
    if purge and "--yes" not in argv:
        out("完整卸载将永久清除 Plus 配置、点击数统计、最近房间和日志。")
        out("数据目录：%s" % DATA_DIR)
        if not sys.stdin or not sys.stdin.isatty():
            out(BAD + "清除数据需要确认；自动化运行请显式添加 --yes。")
            return 1
        try:
            answer = input("输入 yes 确认完整卸载，其他输入取消：").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer != "yes":
            out("已取消，未卸载或清除数据。")
            return 1
    cfg = load()
    elevated = "--elevated" in argv
    if not win.is_admin():
        if elevated:
            out(BAD + "未获得管理员权限，卸载已中止。")
            return 1
        out("移除 WebView2 策略需要管理员权限。")
        out("正在请求管理员权限，请确认系统弹窗...")
        args = ["uninstall", "--elevated"]
        if purge:
            args.extend(["--purge", "--yes"])
        if win.relaunch_elevated(args):
            return 0
        out("未获得管理员授权，未执行卸载。")
        return 1

    app_running = bool(win.processes())
    out("Wuwa Tappo Plus v%s - %s" %
        (__version__, "完整卸载（清除数据）" if purge else "卸载（保留数据）"))
    out("")

    rc = _stop_daemon([])
    if rc:
        return rc

    if win.remove_policy():
        out(OK + "调试策略已移除。")
    else:
        out(WARN + "调试策略不存在，或暂时无法移除。")

    for label, _hive, name, value in win.stray_policy_values(cfg.port):
        out(WARN + "其他策略保留不变：%s\\%s = %s（不属于本工具）"
            % (label, name, value))

    if win.disable_autostart():
        out(OK + "开机自启已移除。")
    else:
        out(WARN + "未注册开机自启。")

    if win.remove_watchdog():
        out(OK + "后台守护任务已移除。")
    else:
        out(WARN + "后台守护任务不存在，或暂时无法移除。")

    if win.read_policy().get(win.TARGET_EXE) or win.read_autostart() or win.read_watchdog():
        out(BAD + "卸载清理未完成，请检查调试策略、开机自启和后台守护任务。")
        return 1

    if app_running:
        try:
            if win.restart_app(cfg.exe) is None:
                out(BAD + "未找到 WuwaTappo.exe，请手动关闭桌宠以清除注入脚本。")
                return 1
        except OSError as exc:
            out(BAD + "无法重启桌宠：%s" % exc)
            return 1
        out(OK + "桌宠已重启，注入脚本已清除。")

    if purge:
        try:
            _purge_data()
        except OSError as exc:
            out(BAD + "程序已卸载，但数据清理失败：%s" % exc)
            return 1
        out("Plus 个人数据已清除。")
    try:
        removed = installation.remove_program()
    except OSError as exc:
        out(BAD + "程序文件清理失败：%s" % exc)
        return 1
    if removed == "deferred":
        out(OK + "退出窗口后将自动删除安装目录中的程序文件。")
    elif removed == "removed":
        out(OK + "安装目录中的程序文件已删除。")
    if purge:
        out("完整卸载完成。")
    else:
        out("卸载完成（保留数据）。配置、统计、最近房间和日志继续保留。")
    return 0


# ------------------------------------------------------------- run/start/stop


def cmd_run(argv):
    from .runner import Runner

    cfg = load()
    log = Log(cfg.log_max_bytes, cfg.log_keep)
    return Runner(cfg, log).run()


def _start_daemon(argv):
    """Launch the daemon after installation has prepared the app."""
    # An explicit start re-arms the watchdog: from now on the daemon is
    # expected to be alive, so losing it is a fault worth repairing.
    _arm()
    state, pid = daemon_status()
    if pid:
        out("Plus 后台已在运行，进程号 %d。" % pid)
        return 0

    from .config import LOCK_PATH

    if LOCK_PATH.exists():
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass
    if (LOG_DIR.parent / ".stop").exists():
        try:
            (LOG_DIR.parent / ".stop").unlink()
        except OSError:
            pass

    win.launch_detached(win.command_args("run", background=True))

    for _ in range(30):
        time.sleep(0.4)
        state, pid = daemon_status()
        if pid:
            out(OK + "Plus 后台已启动，进程号 %d。" % pid)
            out("       日志：%s" % (LOG_DIR / "auto.log"))
            return 0
    out(BAD + "Plus 后台未能启动，请检查日志。")
    return 1


def _stop_daemon(argv):
    """Stop only the Python process during installation or removal."""
    # Stopping is a deliberate act: tell the watchdog to stay out of the way,
    # otherwise it would bring the daemon straight back.
    _disarm()
    state, pid = daemon_status()
    if not pid:
        out("Plus 后台未运行，无需停止。")
        return 0

    stop_file = LOG_DIR.parent / ".stop"
    try:
        stop_file.write_text("stop", encoding="utf-8")
    except OSError:
        pass

    for _ in range(20):
        time.sleep(0.4)
        if not win.pid_alive(pid):
            try:
                stop_file.unlink()
            except OSError:
                pass
            out(OK + "Plus 后台已停止，进程号 %d。" % pid)
            return 0

    if win.kill_pid(pid):
        try:
            stop_file.unlink()
        except OSError:
            pass
        out(OK + "已强制停止 Plus 后台，进程号 %d。" % pid)
    else:
        out(BAD + "无法停止 Plus 后台，进程号 %d。" % pid)
        return 1
    return 0


def cmd_ensure(argv):
    """ensure: start the daemon if it is gone (what the watchdog task runs)"""
    # Runs once a minute under pythonw, so it must be silent, fast, and must
    # never do anything clever. Idempotent by construction: the single-instance
    # lock decides who wins, this only asks "does anybody live there".
    if DISABLED_PATH.exists():
        return 0

    _state, pid = daemon_status()
    if pid or _lock_held():
        return 0

    for leftover in (LOG_DIR.parent / ".stop",):
        try:
            leftover.unlink()
        except OSError:
            pass

    win.launch_detached(win.command_args("run", background=True))
    return 0


# ------------------------------------------------------------------- status


def cmd_status(argv):
    """show countdowns and balance"""
    cfg = load()
    state, pid = daemon_status()
    live, url = live_state(cfg)

    out("Wuwa Tappo Plus v%s - status" % __version__)
    out("")

    if pid:
        started = state.get("started") or ""
        up = ""
        try:
            delta = datetime.now() - datetime.fromisoformat(started)
            up = ", up %s" % _fmt_delta(delta.total_seconds())
        except (ValueError, TypeError):
            pass
        out("daemon     running, pid %d%s" % (pid, up))
    else:
        out("daemon     not running")

    if live:
        version = win.read_policy().get(win.TARGET_EXE, "")
        out("target     %s" % (url or "-"))
        click_state = "paused" if live.get("auto_open") is False else "running"
        if live.get("block") and live.get("auto_open") is not False:
            click_state = "waiting: %s" % live["block"]
        out("click      %s" % (click_state if live.get("inject") else "not ready"))
        out("")
        now = live.get("now") or 0
        out("chest1     %s" % fmt_remain((live.get("left") or 0) - now))
        out("chest2     %s" % fmt_remain((live.get("right") or 0) - now))
        balance = live.get("bal")
        if balance is None:
            out("balance    -  (wallet not painted yet)")
        else:
            out("balance    %d  (enough for %d chest%s)"
                % (balance, balance // 1000, "" if balance // 1000 == 1 else "s"))
            if cfg.alert_critical and balance < cfg.alert_critical:
                out("           below %d - clicking stops under %d, type to earn"
                    % (cfg.alert_critical, cfg.inject_cfg["min_balance"]))
            elif cfg.alert_low and balance < cfg.alert_low:
                out("           below %d - %d chest(s) left before it stops"
                    % (cfg.alert_low, balance // 1000))
        if version:
            out("policy     %s" % version)
    else:
        out("target     not reachable on port %d" % cfg.port)
        if state.get("updated"):
            out("           last seen %s" % state["updated"])
            if state.get("remain_left") is not None:
                out("           then: chest1 %s / chest2 %s / balance %s"
                    % (fmt_remain(state["remain_left"] * 1000),
                       fmt_remain(state["remain_right"] * 1000),
                       state.get("balance")))

    if state.get("clicks_logged") is not None:
        out("clicks     %d logged this session" % state["clicks_logged"])
    if state.get("last_error"):
        out("last error %s" % state["last_error"])
    return 0


def _fmt_delta(seconds):
    seconds = int(seconds)
    if seconds >= 86400:
        return "%dd %dh" % (seconds // 86400, (seconds % 86400) // 3600)
    if seconds >= 3600:
        return "%dh %02dm" % (seconds // 3600, (seconds % 3600) // 60)
    if seconds >= 60:
        return "%dm" % (seconds // 60)
    return "%ds" % seconds


# ------------------------------------------------------------------- doctor


def cmd_doctor(argv):
    """check dependencies and show the config path"""
    if set(argv) - {"--policy", "--clean-policy", "--yes"}:
        out(BAD + " usage: WuwaTappoPlus.exe doctor [--policy | --clean-policy [--yes]]")
        return 1
    if "--yes" in argv and "--clean-policy" not in argv:
        out(BAD + " --yes requires --clean-policy")
        return 1
    if "--policy" in argv or "--clean-policy" in argv:
        return _doctor_policy(argv)
    cfg = load()
    problems = 0

    out("Wuwa Tappo Plus v%s - doctor" % __version__)
    out("")
    out("config        %s" % CONFIG_PATH)
    out("")

    if win.frozen() or win.bundled_python():
        out(OK + " interpreter   %s" % win.interpreter_label())
    else:
        out(WARN + " interpreter   %s" % win.interpreter_label())

    exe = win.find_exe(cfg.exe)
    if exe:
        out(OK + " executable    %s" % exe)
    else:
        out(BAD + " executable    not found; set app.exe in config.toml")
        problems += 1

    policy = win.read_policy()
    value = policy.get(win.TARGET_EXE)
    if value and str(cfg.port) in value:
        out(OK + " policy        %s = %s" % (win.TARGET_EXE, value))
    elif value:
        out(BAD + " policy        holds %r, expected port %d" % (value, cfg.port))
        out("       install will refuse until this is resolved")
        problems += 1
    else:
        out(BAD + " policy        missing; run  WuwaTappoPlus.exe install")
        problems += 1

    leaks = win.stray_policy_values(cfg.port)
    if leaks:
        out(WARN + " policy scope  other values also carry port %d: %s"
            % (cfg.port, ", ".join("%s\\%s" % (l[0], l[2]) for l in leaks)))
        out("       not touched (may predate this install);  WuwaTappoPlus.exe doctor --policy  to inspect")
    else:
        out(OK + " policy scope  only %s" % win.TARGET_EXE)

    version = cdp.probe_port(cfg.port)
    if version:
        out(OK + " debug port    %d (%s)" % (cfg.port, version.get("Browser")))
    else:
        out(BAD + " debug port    %d not responding" % cfg.port)
        problems += 1

    live, url = live_state(cfg)
    if live:
        out(OK + " target        %s" % url)
        want = cfg.inject_cfg["version"]
        if live.get("inject") == want:
            out(OK + " injection     v%s" % live.get("inject"))
        elif live.get("inject"):
            out(WARN + " injection     v%s, config expects v%s" % (live.get("inject"), want))
        else:
            out(BAD + " injection     not installed")
            problems += 1

        balance = live.get("bal")
        need = cfg.inject_cfg["min_balance"]
        if balance is None:
            out(WARN + " balance       -  (wallet not painted yet)")
        elif balance >= need:
            out(OK + " balance       %d >= %d" % (balance, need))
        else:
            out(WARN + " balance       %d < %d, clicks will not fire" % (balance, need))
    else:
        out(BAD + " target        no usable page")
        problems += 1

    state, pid = daemon_status()
    if pid:
        out(OK + " daemon        pid %d" % pid)
    else:
        out(WARN + " daemon        not running")

    if win.read_autostart():
        out(OK + " autostart     registered")
    else:
        out(WARN + " autostart     not registered")

    task = win.read_watchdog()
    if not cfg.watchdog_min:
        out(WARN + " watchdog      off (watchdog_min = 0)")
    elif task:
        note = "  disarmed by a stop" if DISABLED_PATH.exists() else ""
        out(OK + " watchdog      every %d min%s" % (cfg.watchdog_min, note))
    else:
        out(BAD + " watchdog      task missing; run  WuwaTappoPlus.exe install")
        problems += 1

    out("")
    if problems:
        out("%d problem(s) found" % problems)
    else:
        out("all checks passed")
    return 0 if problems == 0 else 1


# --------------------------------------------------------------------- logs


def cmd_logs(argv):
    """logs [n]: tail the log (default 40)"""
    lines = 40
    for a in argv:
        if a.isdigit():
            lines = int(a)
    cfg = load()
    log = Log(cfg.log_max_bytes, cfg.log_keep)
    rows = log.tail(lines)
    if not rows:
        out("no log yet at %s" % log.path)
        return 0
    for row in rows:
        out(row)
    return 0


def _doctor_policy(argv):
    """Inspect policies or explicitly remove confirmed leftovers."""
    cfg = load()
    rows = win.policy_report()
    clean = "--clean-policy" in argv

    out("WebView2 policy key")
    out("  HKLM\\%s" % win.POLICY_KEY)
    out("  HKCU\\%s" % win.POLICY_KEY)
    out("")

    if not rows:
        out("(no values in either hive)")
    else:
        out("%-5s %-26s %s" % ("HIVE", "VALUE NAME", "DATA"))
        for r in rows:
            tag = ""
            if r["wildcard"]:
                tag = "  <- applies to every WebView2 app"
            elif r["ours"]:
                tag = "  <- ours"
            out("%-5s %-26s %s%s" % (r["hive"], r["name"], r["value"], tag))
    out("")

    leaks = win.stray_policy_values(cfg.port)
    ours = [r for r in rows if r["ours"]]

    if not ours:
        out(WARN + "no value named %s; run  WuwaTappoPlus.exe install" % win.TARGET_EXE)
    else:
        out(OK + "ours is present")

    if not leaks:
        out(OK + "no other value carries port %d" % cfg.port)
        return 0

    out(WARN + " %d other value(s) carry port %d:" % (len(leaks), cfg.port))
    for label, _hive, name, value in leaks:
        out("       %s\\%s = %s" % (label, name, value))
    out("")
    out("These may predate this install (another tool, an older experiment).")
    out("They are only reported, never removed automatically.")

    if not clean:
        out("To delete them yourself:  WuwaTappoPlus.exe doctor --clean-policy")
        return 0

    if not win.is_admin():
        out(BAD + "--clean-policy needs Administrator rights")
        return 1

    if "--yes" not in argv:
        if not sys.stdin or not sys.stdin.isatty():
            out(BAD + "refusing to delete without confirmation; add --yes")
            return 1
        sys.stdout.write("  type 'yes' to delete these %d value(s): " % len(leaks))
        sys.stdout.flush()
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n")
            answer = ""
        if answer not in ("yes", "y"):
            out("cancelled, nothing changed")
            return 1

    removed = win.remove_stray_values(cfg.port)
    for item in removed:
        out(OK + "removed  %s" % item)
    return 0 if removed else 1


def cmd_menu(argv):
    """interactive menu (what a double-click opens)"""
    from .menu import main as menu_main

    return menu_main(argv)


COMMANDS = {
    "menu": cmd_menu,
    "run": cmd_run,
    "install": cmd_install,
    "uninstall": cmd_uninstall,
    "ensure": cmd_ensure,
    "status": cmd_status,
    "doctor": cmd_doctor,
    "logs": cmd_logs,
}

PUBLIC_COMMANDS = ("install", "uninstall", "status", "doctor", "logs")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        # No arguments normally means a double-click: show the menu when there
        # is a real console, otherwise fall back to a plain status report.
        if sys.stdin and sys.stdout and sys.stdin.isatty() and sys.stdout.isatty():
            cmd, rest = "menu", []
        else:
            cmd, rest = "status", []
    else:
        cmd = argv[0].lstrip("-")
        rest = argv[1:]

    handler = COMMANDS.get(cmd)
    if handler is None:
        out("Wuwa Tappo Plus v%s" % __version__)
        out("")
        out("usage: WuwaTappoPlus.exe [command]  (no command opens installation manager)")
        out("Daily feature switches are in Wuwa Tappo Settings > Wuwa Tappo Plus.")
        out("License: GPL-3.0-only; corresponding source is provided separately with each release.")
        out("")
        for name in PUBLIC_COMMANDS:
            out("  %-10s %s" % (name, (COMMANDS[name].__doc__ or "").strip()))
        out("")
        out("  doctor --policy         inspect policies, read-only")
        out("  doctor --clean-policy   remove leftovers, asks for confirmation")
        out("  uninstall --purge       clear data after confirmation; --yes for automation")
        return 0 if cmd in ("help", "h") else 1

    try:
        return handler(rest) or 0
    except KeyboardInterrupt:
        out("")
        out("interrupted")
        return 130
