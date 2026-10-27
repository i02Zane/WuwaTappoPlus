"""Command line interface."""

import json
import sys
import time
from datetime import datetime

from . import __version__, cdp, win
from .config import (
    CONFIG_PATH,
    DISABLED_PATH,
    LOG_DIR,
    STATE_PATH,
    load,
)
from .logs import Log

OK = "[ok]  "
BAD = "[fail]"
WARN = "[warn]"


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
            out("still not elevated, aborting")
            return 1
        out("Administrator rights are required to write the WebView2 policy.")
        out("Relaunching with a UAC prompt...")
        if win.relaunch_elevated(["install", "--elevated"]):
            return 0
        out("elevation was refused; nothing changed")
        return 1

    out("Wuwa Tappo Plus v%s - install" % __version__)
    out("")
    out(OK + " interpreter     %s" % win.interpreter_label())

    exe = win.find_exe(cfg.exe)
    if not exe:
        out(BAD + " cannot find WuwaTappo.exe")
        out("       Set app.exe in %s and retry." % CONFIG_PATH)
        return 1
    out(OK + " executable      %s" % exe)

    conflicts = win.policy_conflicts(cfg.port)
    if conflicts:
        out(BAD + " policy conflict, nothing was changed")
        out("")
        for c in conflicts:
            if c["kind"] == "value":
                out("       %s\\%s already holds %s"
                    % (c["hive"], c["name"], c["value"]))
                out("       expected %s" % win.debug_args(cfg.port))
                out("       not written by this tool; overwriting it would "
                    "destroy a setup we know nothing about")
            else:
                out("       %s\\%s also claims port %d: %s"
                    % (c["hive"], c["name"], cfg.port, c["value"]))
                out("       two apps cannot share the port, and CDP could attach "
                    "to the wrong one")
        out("")
        out("  use another port instead:")
        out("      port = %d   in %s" % (cfg.port + 1, CONFIG_PATH))
        out("      then run  WuwaTappoPlus.exe install  again")
        if any(c["kind"] == "value" for c in conflicts):
            out("")
            out("  or delete that value if you are sure it is dead:")
            out("      WuwaTappoPlus.exe uninstall   (removes only the value under "
                "%s)" % win.TARGET_EXE)
        if any(c["kind"] == "port" for c in conflicts):
            out("")
            out("  or free the port:")
            out("      WuwaTappoPlus.exe doctor --policy         inspect, read-only")
            out("      WuwaTappoPlus.exe doctor --clean-policy delete, asks for confirmation")
        return 1

    rc = _stop_daemon([])
    if rc:
        return rc

    _write_config_exe(exe)
    args = win.write_policy(cfg.port)
    out(OK + " policy          %s = %s" % (win.TARGET_EXE, args))

    try:
        if win.processes():
            out("       restarting the app to load policy and page configuration...")
            if win.restart_app(exe) is None:
                out(BAD + " cannot find WuwaTappo.exe for restart")
                return 1
        else:
            win.launch_app(exe)
        out(OK + " app ready")
    except OSError as exc:
        out(BAD + " app launch/restart failed: %s" % exc)
        return 1

    out("       waiting for the debug port...")
    deadline = time.time() + 25
    version = None
    while time.time() < deadline:
        version = cdp.probe_port(cfg.port)
        if version:
            break
        time.sleep(1.0)

    if version:
        out(OK + " debug port      %d (%s)" % (cfg.port, version.get("Browser")))
    else:
        out(BAD + " debug port      %d never opened" % cfg.port)
        out("       The app may need a full restart. Run: WuwaTappoPlus.exe install")
        _finish_elevated(elevated)
        return 1

    cmd = win.enable_autostart()
    out(OK + " autostart       %s" % cmd)

    minutes = cfg.watchdog_min
    if minutes > 0:
        if win.register_watchdog(minutes):
            out(OK + " watchdog        every %d min, restarts a lost daemon"
                % minutes)
        else:
            out(WARN + " watchdog        scheduled task could not be created")
            out("       run this from an elevated prompt to try again")
    elif win.remove_watchdog():
        out(WARN + " watchdog        turned off in config.toml")

    rc = _start_daemon([])
    out("")
    out("Done. Open Wuwa Tappo Settings > Wuwa Tappo Plus for feature switches.")
    _finish_elevated(elevated)
    return rc


def _finish_elevated(elevated):
    if elevated and sys.stdin and sys.stdin.isatty():
        try:
            input("\nPress Enter to close this window...")
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


def cmd_uninstall(argv):
    """uninstall Plus and clear injected code; keep preferences"""
    cfg = load()
    elevated = "--elevated" in argv
    if not win.is_admin():
        if elevated:
            out(BAD + " still not elevated, aborting")
            return 1
        out("Administrator rights are required to remove the WebView2 policy.")
        args = ["uninstall", "--elevated"]
        return 0 if win.relaunch_elevated(args) else 1

    app_running = bool(win.processes())
    out("Wuwa Tappo Plus v%s - uninstall" % __version__)
    out("")

    rc = _stop_daemon([])
    if rc:
        return rc

    if win.remove_policy():
        out(OK + " policy removed")
    else:
        out(WARN + " policy value was not present or could not be removed")

    for label, _hive, name, value in win.stray_policy_values(cfg.port):
        out(WARN + " policy note      %s\\%s = %s (not ours, left alone)"
            % (label, name, value))

    if win.disable_autostart():
        out(OK + " autostart removed")
    else:
        out(WARN + " autostart was not registered")

    if win.remove_watchdog():
        out(OK + " watchdog removed")
    else:
        out(WARN + " watchdog task could not be removed")

    if win.read_policy().get(win.TARGET_EXE) or win.read_autostart() or win.read_watchdog():
        out(BAD + " cleanup incomplete; check policy, autostart and watchdog")
        return 1

    if app_running:
        try:
            if win.restart_app(cfg.exe) is None:
                out(BAD + " cannot find WuwaTappo.exe; close the app manually")
                return 1
        except OSError as exc:
            out(BAD + " could not restart the app: %s" % exc)
            return 1
        out(OK + " app restarted, injected code cleared")

    out("")
    out("Uninstalled. Policy and startup entries removed; preferences kept.")
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
        out("already running, pid %d" % pid)
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
            out(OK + " daemon started, pid %d" % pid)
            out("       log: %s" % (LOG_DIR / "auto.log"))
            return 0
    out(BAD + " daemon did not start, check the log")
    return 1


def _stop_daemon(argv):
    """Stop only the Python process during installation or removal."""
    # Stopping is a deliberate act: tell the watchdog to stay out of the way,
    # otherwise it would bring the daemon straight back.
    _disarm()
    state, pid = daemon_status()
    if not pid:
        out("not running")
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
            out(OK + " stopped, pid %d" % pid)
            return 0

    if win.kill_pid(pid):
        try:
            stop_file.unlink()
        except OSError:
            pass
        out(OK + " force stopped, pid %d" % pid)
    else:
        out(BAD + " could not stop pid %d" % pid)
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
        return 0 if cmd in ("help", "h") else 1

    try:
        return handler(rest) or 0
    except KeyboardInterrupt:
        out("")
        out("interrupted")
        return 130
