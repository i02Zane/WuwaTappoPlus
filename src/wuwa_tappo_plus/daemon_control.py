"""Single-instance daemon discovery, startup, shutdown and watchdog recovery."""

import json
import time

from . import win
from .config import DISABLED_PATH, LOCK_PATH, LOG_DIR, STATE_PATH
from .operations import Progress

STOP_FILE = LOG_DIR.parent / ".stop"


def read_state():
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def status():
    state = read_state()
    pid = state.get("pid")
    alive = type(pid) is int and pid > 0 and win.pid_alive(pid)
    return state, pid if alive else None


def _arm():
    """Clear the 'stopped on purpose' marker so the watchdog may revive us."""
    DISABLED_PATH.unlink(missing_ok=True)


def _disarm():
    """Mark a deliberate stop. Without it the watchdog would fight the user."""
    DISABLED_PATH.parent.mkdir(parents=True, exist_ok=True)
    DISABLED_PATH.write_text("stopped by user\n", encoding="utf-8")


def lock_held():
    """True when a daemon already owns the single-instance lock.

    state.json is written a moment after the lock is taken, so right after a
    start it can still show the previous, dead pid. Probing the lock itself is
    the only race-free answer to 'is somebody already there'.
    """
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


def start(callback=None):
    progress = Progress(callback)
    progress.stage = "start_daemon"
    try:
        _arm()
        _, pid = status()
        if pid:
            return progress.add("skipped", "Plus 后台已在运行，进程号 %d。" % pid)
        # Never unlink an active lock or spawn another process while it starts.
        if not lock_held():
            STOP_FILE.unlink(missing_ok=True)
            win.launch_detached(win.command_args("run", background=True))
        for _ in range(30):
            time.sleep(0.4)
            _, pid = status()
            if pid:
                return progress.add("completed", "Plus 后台已启动，进程号 %d。" % pid)
        return progress.add("failed", "Plus 后台未能启动，请检查日志。")
    except OSError as exc:
        return progress.add("failed", "后台启动失败：%s" % exc)


def stop(callback=None):
    progress = Progress(callback)
    progress.stage = "stop_daemon"
    try:
        _disarm()
        _, pid = status()
        if not pid:
            if lock_held():
                return progress.add("failed", "后台实例锁仍被占用，无法确认进程，已中止操作。")
            return progress.add("skipped", "Plus 后台未运行，无需停止。")
        STOP_FILE.write_text("stop", encoding="utf-8")
        for _ in range(20):
            time.sleep(0.4)
            if not win.pid_alive(pid) and not lock_held():
                STOP_FILE.unlink(missing_ok=True)
                return progress.add("completed", "Plus 后台已停止，进程号 %d。" % pid)
        # A dead PID with a held lock may belong to a replacement process.
        if win.pid_alive(pid):
            win.kill_pid(pid)
        if win.pid_alive(pid) or lock_held():
            return progress.add("failed", "后台进程或实例锁仍未释放，已中止操作。")
        STOP_FILE.unlink(missing_ok=True)
        return progress.add("completed", "Plus 后台已退出，进程号 %d（等待超时后确认）。" % pid)
    except OSError as exc:
        return progress.add("failed", "后台停止失败：%s" % exc)


def ensure():
    """Silent watchdog entry; the daemon's own lock resolves competing starts."""
    if DISABLED_PATH.exists():
        return 0
    _, pid = status()
    if pid or lock_held():
        return 0
    STOP_FILE.unlink(missing_ok=True)
    win.launch_detached(win.command_args("run", background=True))
    return 0
