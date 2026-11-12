"""Command line interface."""

import sys
from datetime import datetime

from . import __version__, cdp, console, daemon_control, installation, lifecycle, win
from .operations import OperationResult, StepResult
from .config import (
    CONFIG_PATH,
    DISABLED_PATH,
    LOG_DIR,
    load,
)
from .logs import Log
from .paths import DATA_DIR

OK = "[完成] "
BAD = "[失败] "
WARN = "[提示] "


def out(text=""):
    console.write_line(text)


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

class CommandExit(int):
    """Numeric CLI exit status carrying the explicit outcome for the menu."""

    def __new__(cls, result):
        code = super().__new__(cls, result.exit_code)
        code.operation = result
        return code


def _show_step(step):
    prefix = {"completed": OK, "skipped": WARN, "warning": WARN, "failed": BAD}[step.status]
    out(prefix + step.message)


def _handoff(argv, action):
    """Return a result only when the command cannot execute in this process."""
    if win.is_admin():
        return None
    if "--elevated" in argv:
        message = "未获得管理员权限，操作已中止。"
    else:
        out("修改 WebView2 策略需要管理员权限。")
        out("正在请求管理员权限，请确认系统弹窗...")
        args = [action, "--elevated"]
        if action == "uninstall" and "--purge" in argv:
            args.extend(["--purge", "--yes"])
        if win.relaunch_elevated(args):
            return OperationResult("handed_off")
        message = "未获得管理员授权，未执行操作。"
    step = StepResult("elevation", "failed", message)
    _show_step(step)
    return OperationResult("failed", [step])


def cmd_install(argv):
    """install or repair Plus; preserve feature switches"""
    if installation.managed():
        try:
            installation.launch_setup()
            return CommandExit(OperationResult("handed_off"))
        except OSError as exc:
            step = StepResult("setup", "failed", str(exc))
            _show_step(step)
            return CommandExit(OperationResult("failed", [step]))
    handoff = _handoff(argv, "install")
    if handoff is not None:
        return CommandExit(handoff)
    out("Wuwa Tappo Plus v%s - 安装 / 修复" % __version__)
    out(OK + "运行环境：%s" % win.interpreter_label())
    result = lifecycle.install(load(), _show_step)
    if result.exit_code == 0:
        out("安装完成。请打开桌宠的「设置 → Wuwa Tappo Plus」调整功能开关。")
        _finish_elevated("--elevated" in argv)
    else:
        out("安装未完成，请根据上方提示排查后重新执行安装 / 修复。")
    return CommandExit(result)


def _finish_elevated(elevated):
    if elevated and sys.stdin and sys.stdin.isatty():
        try:
            input("\n按 Enter 键关闭此窗口...")
        except EOFError:
            pass


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
    if installation.managed():
        try:
            installation.launch_uninstaller(purge, silent="--yes" in argv)
            return CommandExit(OperationResult("handed_off"))
        except OSError as exc:
            step = StepResult("uninstaller", "failed", str(exc))
            _show_step(step)
            return CommandExit(OperationResult("failed", [step]))
    handoff = _handoff(argv, "uninstall")
    if handoff is not None:
        return CommandExit(handoff)
    out("Wuwa Tappo Plus v%s - %s" %
        (__version__, "完整卸载（清除数据）" if purge else "卸载（保留数据）"))
    result = lifecycle.uninstall(purge, _show_step)
    if result.exit_code == 0:
        out("完整卸载完成。" if purge else "卸载完成（保留数据）。配置、统计、最近房间和日志继续保留。")
    return CommandExit(result)


# ------------------------------------------------------------- run/start/stop


def cmd_run(argv):
    from .runner import Runner

    cfg = load()
    log = Log(cfg.log_max_bytes, cfg.log_keep)
    return Runner(cfg, log).run()


def cmd_ensure(argv):
    """ensure: start the daemon if it is gone (what the watchdog task runs)"""
    return daemon_control.ensure()


# ------------------------------------------------------------------- status


def cmd_status(argv):
    """show countdowns and balance"""
    cfg = load()
    state, pid = daemon_control.status()
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

    state, pid = daemon_control.status()
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
