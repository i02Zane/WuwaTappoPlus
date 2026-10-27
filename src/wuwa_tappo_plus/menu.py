"""Interactive console menu for double-click use.

Double-clicking WuwaTappoPlus.exe lands here: a live status panel plus a numbered action
list. Keys are read through msvcrt, so choosing an action takes one keystroke
and no Enter. Falls back to a line-based prompt when there is no real console.
"""

import os
import re
import sys
import time
import traceback
import unicodedata
from datetime import datetime

from . import __version__, cli, win
from .config import load

try:
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows
    msvcrt = None

BOLD = "\x1b[1m"
DIM = "\x1b[90m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"
RESET = "\x1b[0m"

COLOR = False
WIDTH = 58


# ------------------------------------------------------------------ console


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _paint(code, text):
    return code + text + RESET if COLOR else text


def _visible(text):
    """Drop escape sequences so alignment maths ignore colour codes."""
    return ANSI_RE.sub("", text) if "\x1b" in text else text


def _wide(text):
    """Display width, counting CJK glyphs as two columns."""
    total = 0
    for ch in _visible(text):
        total += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
    return total


def _pad(text, width):
    gap = width - _wide(text)
    return text + " " * gap if gap > 0 else text


def _row(left, right, width=WIDTH):
    gap = width - _wide(left) - _wide(right)
    return left + " " * max(1, gap) + right


def _enable_vt():
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.GetStdHandle(-11)
        mode = wintypes.DWORD()
        if not k32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True
        return bool(k32.SetConsoleMode(handle, mode.value |
                                       ENABLE_VIRTUAL_TERMINAL_PROCESSING))
    except Exception:
        return False


def _set_title(text):
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleTitleW(text)
    except Exception:
        pass


def _clear():
    if COLOR:
        sys.stdout.write("\x1b[2J\x1b[H")
    else:
        os.system("cls" if os.name == "nt" else "clear")
    sys.stdout.flush()


# ------------------------------------------------------------------- state


def _remain(seconds):
    if seconds is None:
        return "-"
    if seconds <= 0:
        return "可开启"
    seconds = int(seconds)
    if seconds < 60:
        return "%d 秒" % seconds
    if seconds < 3600:
        return "%d 分 %02d 秒" % (seconds // 60, seconds % 60)
    return "%d 时 %02d 分" % (seconds // 3600, (seconds % 3600) // 60)


def _ago(seconds):
    if seconds is None:
        return "无数据"
    if seconds < 2:
        return "刚刚"
    if seconds < 90:
        return "%d 秒前" % int(seconds)
    return "%d 分前" % int(seconds // 60)


def _age(updated):
    try:
        delta = datetime.now() - datetime.fromisoformat(str(updated))
        return max(0.0, delta.total_seconds())
    except (ValueError, TypeError):
        return None


def _collect():
    st = cli.read_state()
    pid = st.get("pid")
    try:
        pid = int(pid) if pid else None
    except (TypeError, ValueError):
        pid = None
    alive = bool(pid) and win.pid_alive(pid)

    try:
        app_running = bool(win.processes())
    except Exception:
        app_running = False

    now_ms = time.time() * 1000.0
    left = st.get("left") or 0
    right = st.get("right") or 0
    balance = st.get("balance")
    up = None
    if alive and st.get("started"):
        try:
            up = (datetime.now() - datetime.fromisoformat(st["started"])).total_seconds()
        except (ValueError, TypeError):
            up = None

    return {
        "daemon_alive": alive,
        "auto_open": st.get("auto_open", True),
        "pid": pid if alive else None,
        "up": up,
        "app_running": app_running,
        "inject": st.get("inject") or 0,
        # A countdown is only meaningful while something is watching it. With
        # the daemon gone the timestamps still tick, but nothing would act on
        # them, so the panel says "-" instead of implying a live reading.
        "remain_left": (left - now_ms) / 1000.0 if (alive and left) else None,
        "remain_right": (right - now_ms) / 1000.0 if (alive and right) else None,
        "balance": balance if alive else None,
        "age": _age(st.get("updated")),
        "last_error": st.get("last_error"),
        "clicks": st.get("clicks_logged"),
        "target": st.get("target"),
        "port": st.get("port") or load().port,
    }


# ------------------------------------------------------------------- panel


def _onoff(flag, good, bad):
    return _paint(GREEN, good) if flag else _paint(RED, bad)


def _frame(data):
    clock = datetime.now().strftime("%H:%M:%S")
    lines = [""]

    lines.append("  " + _row(_paint(BOLD, "Wuwa Tappo Plus v%s · 安装管理" % __version__), _paint(DIM, clock)))
    lines.append("  " + _paint(DIM, "-" * WIDTH))

    if data["daemon_alive"]:
        detail = "运行中"
        if data.get("auto_open") is False:
            detail += "（自动开箱已暂停）"
        if data["pid"]:
            detail += "  pid %d" % data["pid"]
        if data["up"]:
            detail += "  已运行 %s" % _remain(data["up"])
        daemon = _paint(GREEN, detail)
    else:
        daemon = _paint(RED, "未运行")
    lines.append("  " + _paint(DIM, _pad("守护进程", 10)) + daemon)

    lines.append("  " + _paint(DIM, _pad("桌宠程序", 10))
                 + _onoff(data["app_running"], "运行中", "未启动"))

    lines.append("  " + _paint(DIM, "-" * WIDTH))

    # ---- warnings
    warn = []
    if not data["app_running"]:
        warn.append("桌宠程序未启动，可按 [1] 安装 / 修复")
    elif not data["daemon_alive"]:
        warn.append("Plus 后台未运行，可按 [1] 安装 / 修复")
    elif data["age"] is not None and data["age"] > 20:
        warn.append("状态已 %s 未更新，可按 [1] 修复" % _ago(data["age"]))
    if data["last_error"]:
        warn.append("最近错误：%s" % str(data["last_error"])[:44])
    for text in warn:
        lines.append("  " + _paint(YELLOW, "! " + text))

    if warn:
        lines.append("")

    # ---- actions
    lines.append("  " + _paint(BOLD, "[1]") + " 安装 / 修复  "
                 + _paint(BOLD, "[2]") + " 卸载       "
                 + _paint(BOLD, "[0]") + " 退出")
    lines.append("")
    lines.append("  " + _paint(DIM, "日常开关请到桌宠的「设置 → Wuwa Tappo Plus」"))
    lines.append("  " + _paint(DIM, "直接按数字键，卸载保留个人配置"))
    lines.append("")
    return lines


def _draw(data):
    sys.stdout.write("\x1b[H")
    for line in _frame(data):
        sys.stdout.write(line + "\x1b[K\n")
    sys.stdout.write("\x1b[J")
    sys.stdout.flush()


# ------------------------------------------------------------------ actions


def _wait_key(prompt="按任意键返回菜单..."):
    sys.stdout.write("  " + _paint(DIM, prompt))
    sys.stdout.flush()
    try:
        if msvcrt:
            msvcrt.getwch()
        else:
            input()
    except (KeyboardInterrupt, EOFError):
        pass
    sys.stdout.write("\n")
    sys.stdout.flush()


class _Indent:
    """Wrap stdout so command output lines up with the panel."""

    def __init__(self, stream, prefix="  "):
        self._stream = stream
        self._prefix = prefix
        self._at_start = True

    def write(self, text):
        text = str(text)
        if not text:
            return 0
        out = []
        for line in text.splitlines(True):
            body = line.rstrip("\r\n")
            ending = line[len(body):]
            if body and self._at_start:
                out.append(self._prefix)
            out.append(body)
            out.append(ending)
            self._at_start = bool(ending)
        self._stream.write("".join(out))
        return len(text)

    def flush(self):
        self._stream.flush()

    def isatty(self):
        return self._stream.isatty()

    def fileno(self):
        return self._stream.fileno()

    @property
    def encoding(self):
        return getattr(self._stream, "encoding", "utf-8")


def _run(title, handler):
    """Run one cli handler on a clean screen, then wait for a key."""
    _clear()
    sys.stdout.write("  " + _paint(BOLD, title) + "\n")
    sys.stdout.write("  " + _paint(DIM, "-" * WIDTH) + "\n\n")
    sys.stdout.flush()

    real = sys.stdout
    rc = 0
    sys.stdout = _Indent(real)
    try:
        rc = handler([])
    except SystemExit as exc:
        rc = exc.code
    except KeyboardInterrupt:
        rc = 130
    except Exception:
        sys.stdout.write(_paint(RED, "出错：") + "\n")
        sys.stdout.write(traceback.format_exc())
        rc = 1
    finally:
        sys.stdout = real

    sys.stdout.write("\n")
    if rc not in (0, None):
        sys.stdout.write("  " + _paint(YELLOW, "返回码 %s" % rc) + "\n")
    sys.stdout.flush()
    _wait_key()


def _install():
    _run("安装 / 修复", cli.cmd_install)


def _uninstall():
    # Cleanup is still needed when policy/startup exists but no process is alive.
    _run("卸载", cli.cmd_uninstall)


ACTIONS = {
    "1": ("安装 / 修复", _install),
    "2": ("卸载", _uninstall),
}


# --------------------------------------------------------------------- loop


def _getch(timeout):
    """Return one key ('' for a special key), or None when the wait expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                msvcrt.getwch()
                return ""
            return ch
        time.sleep(0.04)
    return None


def main(argv):
    global COLOR
    argv = list(argv or [])
    once = "--once" in argv
    interactive = bool(sys.stdin.isatty() and sys.stdout.isatty()) and msvcrt is not None

    if once:
        # Render a single frame and leave: used to check the layout in a pipe.
        COLOR = "--color" in argv
        for line in _frame(_collect()):
            sys.stdout.write(line + "\n")
        return 0

    if not interactive:
        COLOR = False
        return _plain_menu()

    COLOR = _enable_vt() and "--no-color" not in argv
    _set_title("Wuwa Tappo Plus")
    sys.stdout.write("\x1b[2J\x1b[?25l")
    sys.stdout.flush()

    try:
        _draw(_collect())
        while True:
            key = _getch(1.0)
            if key is None:
                _draw(_collect())
                continue
            if key in ("0", "q", "Q", "\x1b", "\x03"):
                break
            entry = ACTIONS.get(key)
            if entry is None:
                continue
            entry[1]()
            sys.stdout.write("\x1b[2J")
            sys.stdout.flush()
            _draw(_collect())
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[0m\x1b[?25h")
        sys.stdout.flush()
    print("  已退出。")
    return 0


def _plain_menu():
    """Fallback for redirected output or a console without msvcrt."""
    while True:
        data = _collect()
        for line in _frame(data):
            print(line)
        print()
        try:
            choice = input("  选择 [1-2, 0=退出]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice in ("0", "q", "Q", ""):
            return 0
        entry = ACTIONS.get(choice)
        if entry is None:
            continue
        try:
            entry[1]()
        except Exception:
            traceback.print_exc()
