"""Windows integration: WebView2 debug policy, autostart, discovery, processes.

The debug port is enabled through the official WebView2 policy key, which the
runtime appends to whatever AdditionalBrowserArguments the host app already
set. That is why it works despite Tauri hard-coding its own arguments.
"""

import ctypes
import os
import subprocess
import time
import sys
import threading
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT
from .paths import APP_DIR, frozen

POLICY_KEY = r"Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments"
POLICY_ROOT = r"Software\Policies\Microsoft\Edge\WebView2"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "WuwaTappoPlus"
TARGET_EXE = "WuwaTappo.exe"

UNINSTALL_KEYS = [
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", None),
    (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", None),
]

PRESET_DIRS = [
    r"D:\Program Files\WuwaTappo",
    r"C:\Program Files\WuwaTappo",
    r"C:\Program Files (x86)\WuwaTappo",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\WuwaTappo"),
    os.path.expandvars(r"%LOCALAPPDATA%\WuwaTappo"),
]


def _winreg():
    import winreg

    return winreg


def desktop_directory():
    """Resolve the Windows desktop, including OneDrive/redirection."""
    if os.name != "nt":
        return Path.home() / "Desktop"
    folder = ctypes.create_unicode_buffer(32768)
    result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, folder)
    if result != 0 or not folder.value:
        raise OSError("无法定位桌面，请检查 Windows 桌面路径。")
    return Path(folder.value)


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


_launch_executable = None


def relaunch_elevated(argv):
    """Re-run this process with a UAC prompt. Returns True if launched."""
    from .installation import directory
    command = [sys.executable] if frozen() else command_args()
    command.extend(["--data-dir", str(ROOT), "--program-dir", str(directory()), *argv])
    params = subprocess.list2cmdline(command[1:])
    previous_reset = os.environ.get("PYINSTALLER_RESET_ENVIRONMENT")
    previous_path = os.environ.get("PATH")
    if frozen():
        environment = _child_environment(reset=True)
        os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        if "PATH" in environment:
            os.environ["PATH"] = environment["PATH"]
    try:
        with _system_dll_search():
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", command[0], params, str(APP_DIR), 1
            )
        return int(rc) > 32
    except Exception:
        return False
    finally:
        if frozen():
            if previous_reset is None:
                os.environ.pop("PYINSTALLER_RESET_ENVIRONMENT", None)
            else:
                os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = previous_reset
            if previous_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = previous_path


RUNTIME_DIR = APP_DIR / "runtime"


def bundled_python(name="python.exe"):
    """The copy of Python shipped inside the project, if it is there.

    Depending on whatever `python` happens to be on PATH is fragile: on this
    machine the first hit is the editor's own bundled interpreter, which moves
    when it updates. A project-local runtime keeps the app self-contained.
    """
    exe = RUNTIME_DIR / name
    return exe if exe.exists() else None


def pythonw():
    if frozen():
        return sys.executable
    bundled = bundled_python("pythonw.exe")
    if bundled:
        return str(bundled)
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def interpreter_label():
    """Human readable: which interpreter this project runs on."""
    if frozen():
        return "%s（单文件内置环境）" % sys.executable
    bundled = bundled_python()
    if bundled:
        return "%s（内置 Python）" % bundled
    return "%s（系统 Python）" % sys.executable


def command_args(*args, background=False):
    """Build the same application command in source and frozen distributions."""
    if frozen():
        return [str(_launch_executable or sys.executable), *map(str, args)]
    interpreter = pythonw() if background else sys.executable
    launcher = APP_DIR / "launch.py"
    entry = [str(launcher)] if launcher.is_file() else ["-m", "wuwa_tappo_plus"]
    return [interpreter, *entry, *map(str, args)]


# ---------------------------------------------------------------- discovery


def _from_uninstall():
    winreg = _winreg()
    found = []
    for path, _ in UNINSTALL_KEYS:
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                root = winreg.OpenKey(hive, path)
            except OSError:
                continue
            try:
                index = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        key = winreg.OpenKey(root, sub)
                        name = winreg.QueryValueEx(key, "DisplayName")[0]
                    except OSError:
                        continue
                    if not isinstance(name, str):
                        continue
                    if "tappo" not in name.lower() and "wuwa" not in name.lower():
                        continue
                    for value_name in ("InstallLocation", "DisplayIcon"):
                        try:
                            value = winreg.QueryValueEx(key, value_name)[0]
                        except OSError:
                            continue
                        if not isinstance(value, str) or not value:
                            continue
                        value = value.strip('"')
                        candidate = Path(value)
                        if candidate.suffix.lower() == ".exe":
                            if candidate.exists():
                                found.append(candidate)
                        elif candidate.is_dir():
                            exe = candidate / TARGET_EXE
                            if exe.exists():
                                found.append(exe)
            finally:
                root.Close()
    return found


def processes():
    """[(pid, Path)] for every running WuwaTappo.exe."""
    k32 = _process_api()

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    INVALID = ctypes.c_void_p(-1).value
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        function = getattr(k32, name)
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        function.restype = wintypes.BOOL

    found = []
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID:
        raise _process_error("CreateToolhelp32Snapshot", 0, ctypes.get_last_error())
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == TARGET_EXE.lower():
                handle = k32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION | 0x00100000, False, entry.th32ProcessID
                )
                if not handle and ctypes.get_last_error() != 87:
                    raise _process_error("OpenProcess", entry.th32ProcessID, ctypes.get_last_error())
                if handle:
                    try:
                        buf = ctypes.create_unicode_buffer(32768)
                        size = wintypes.DWORD(32768)
                        if k32.QueryFullProcessImageNameW(
                            handle, 0, buf, ctypes.byref(size)
                        ):
                            found.append((entry.th32ProcessID, Path(buf.value)))
                        else:
                            error = ctypes.get_last_error()
                            if not _process_exited(k32, handle, 0, entry.th32ProcessID):
                                raise _process_error("QueryFullProcessImageNameW", entry.th32ProcessID, error)
                    finally:
                        k32.CloseHandle(handle)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise _process_error("Process32FirstW/Process32NextW", 0, ctypes.get_last_error())
    finally:
        k32.CloseHandle(snap)
    return found


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]


def _token_api():
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    api.OpenProcessToken.restype = wintypes.BOOL
    api.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.GetTokenInformation.restype = wintypes.BOOL
    api.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    api.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    api.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    api.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    return api


@dataclass(frozen=True)
class ProcessPermissions:
    pid: int
    elevated: bool
    integrity: int
    stop_error: int = 0


def process_permissions(pid, expected_exe):
    """Inspect the actual token and request stop rights without stopping anything."""
    api = _process_api()
    rights = 0x1000 | 0x00100000  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    handle = api.OpenProcess(rights | 1, False, pid)
    stop_error = 0
    if not handle:
        stop_error = ctypes.get_last_error()
        if stop_error == 87:
            return None
        handle = api.OpenProcess(rights, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:
            return None
        raise _process_error("OpenProcess", pid, error)
    token = wintypes.HANDLE()
    try:
        if _process_exited(api, handle, 0, pid):
            return None
        image = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(image))
        if not api.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
            raise _process_error("QueryFullProcessImageNameW", pid, ctypes.get_last_error())
        if os.path.normcase(os.path.normpath(image.value)) != os.path.normcase(os.path.normpath(str(expected_exe))):
            raise OSError("进程 %d 的程序路径已变化，已中止权限检查。" % pid)
        security = _token_api()
        if not security.OpenProcessToken(handle, 8, ctypes.byref(token)):  # TOKEN_QUERY
            raise _process_error("OpenProcessToken", pid, ctypes.get_last_error())
        elevated = wintypes.DWORD()
        length = wintypes.DWORD()
        if not security.GetTokenInformation(token, 20, ctypes.byref(elevated), ctypes.sizeof(elevated), ctypes.byref(length)):
            raise _process_error("GetTokenInformation(TokenElevation)", pid, ctypes.get_last_error())
        security.GetTokenInformation(token, 25, None, 0, ctypes.byref(length))
        if ctypes.get_last_error() != 122 or length.value < ctypes.sizeof(_SidAndAttributes):
            raise _process_error("GetTokenInformation(TokenIntegrityLevel)", pid, ctypes.get_last_error())
        label = ctypes.create_string_buffer(length.value)
        if not security.GetTokenInformation(token, 25, label, len(label), ctypes.byref(length)):
            raise _process_error("GetTokenInformation(TokenIntegrityLevel)", pid, ctypes.get_last_error())
        sid = ctypes.cast(label, ctypes.POINTER(_SidAndAttributes)).contents.sid
        count = security.GetSidSubAuthorityCount(sid) if sid else None
        if not count or not count[0]:
            raise OSError("进程 %d 的完整性级别 SID 无效。" % pid)
        rid = security.GetSidSubAuthority(sid, count[0] - 1)
        if not rid:
            raise OSError("进程 %d 的完整性级别无法读取。" % pid)
        return ProcessPermissions(pid, bool(elevated.value), rid[0], stop_error)
    finally:
        if token.value:
            api.CloseHandle(token)
        api.CloseHandle(handle)


def check_app_permissions(callback=None):
    running = False
    for pid, path in processes():
        permissions = process_permissions(pid, path)
        if permissions is None:
            continue
        running = True
        level = {0: "不受信任", 0x1000: "低", 0x2000: "中", 0x2100: "中高",
                 0x3000: "高", 0x4000: "系统"}.get(permissions.integrity, "0x%X" % permissions.integrity)
        if callback:
            callback("桌宠 PID %d：%s，完整性级别：%s，%s。" % (
                pid, "已提升" if permissions.elevated else "未提升", level,
                "当前安装进程无权关闭" if permissions.stop_error else "当前安装进程可关闭"))
        if permissions.stop_error:
            raise OSError("%s；请先退出桌宠，再重新运行安装包。" %
                          _process_error("OpenProcess(PROCESS_TERMINATE)", pid, permissions.stop_error))
    return running


def find_exe(hint=""):
    """Locate WuwaTappo.exe. Returns a Path or None."""
    if hint:
        p = Path(hint)
        if p.is_file():
            return p
        if p.is_dir() and (p / TARGET_EXE).exists():
            return p / TARGET_EXE

    for _, candidate in processes():
        if candidate.exists():
            return candidate

    for candidate in _from_uninstall():
        if candidate.exists():
            return candidate

    for folder in PRESET_DIRS:
        exe = Path(folder) / TARGET_EXE
        if exe.exists():
            return exe

    return None


def running_pids():
    return [pid for pid, _ in processes()]


# ------------------------------------------------------------------ policy


def _enum_policy(hive):
    """Returns {value_name: args} under the debug policy key of one hive."""
    winreg = _winreg()
    out = {}
    try:
        key = winreg.OpenKey(hive, POLICY_KEY)
    except OSError:
        return out
    try:
        index = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            out[name] = value
    finally:
        key.Close()
    return out


def read_policy():
    """The machine-wide (HKLM) values. This is the set that actually applies."""
    return _enum_policy(_winreg().HKEY_LOCAL_MACHINE)


def debug_args(port):
    return "--remote-debugging-port=%d" % port


def policy_report():
    """Read-only view of both hives. **Never modifies anything.**

    The key is machine-wide and may already contain entries this project did not
    write (another copy of this tool, a vendor's own debugging setup, a leftover
    from some other automation). Deciding what is disposable is the user's call,
    so everything here is inspection only.
    """
    winreg = _winreg()
    rows = []
    for label, hive in (("HKLM", winreg.HKEY_LOCAL_MACHINE),
                        ("HKCU", winreg.HKEY_CURRENT_USER)):
        for name, value in _enum_policy(hive).items():
            name = str(name)
            rows.append({
                "hive": label,
                "name": name,
                "value": str(value),
                "ours": name.strip().lower() == TARGET_EXE.lower(),
                "wildcard": name.strip() == "*",
            })
    return rows


def stray_policy_values(port):
    """Values that would leak our port into apps we do not own.

    The runtime matches a value name against the host executable, except for the
    literal '*' which applies to every WebView2 app on the box. Reported, never
    removed automatically - such an entry may well predate this project.
    """
    winreg = _winreg()
    args = debug_args(port)
    leaks = []
    for label, hive in (("HKLM", winreg.HKEY_LOCAL_MACHINE),
                        ("HKCU", winreg.HKEY_CURRENT_USER)):
        for name, value in _enum_policy(hive).items():
            if name.strip().lower() == TARGET_EXE.lower():
                continue
            if args in str(value):
                leaks.append((label, hive, name, str(value)))
    return leaks


def remove_stray_values(port):
    """Delete wildcard / foreign values carrying our exact port argument.

    Only called from an explicit `WuwaTappoPlus.exe doctor --clean-policy`, never during install.
    Scoped tightly: only values whose data mentions our own port are touched.
    """
    winreg = _winreg()
    removed = []
    for label, hive, name, _value in stray_policy_values(port):
        try:
            key = winreg.OpenKey(hive, POLICY_KEY, 0, winreg.KEY_ALL_ACCESS)
        except OSError:
            continue
        try:
            winreg.DeleteValue(key, name)
            removed.append("%s\\%s" % (label, name))
            left = winreg.QueryInfoKey(key)[1]
        except OSError:
            left = 1
        finally:
            key.Close()
        if left == 0:
            for path in (POLICY_KEY, POLICY_ROOT):
                try:
                    winreg.DeleteKey(hive, path)
                except OSError:
                    pass
    return removed


def policy_conflicts(port, exe_name=TARGET_EXE):
    """Reasons install must refuse to run. **Read-only.**

    Two different problems, both of which mean "do not touch anything":

    ``value``  a value under our own name already holds different arguments.
               Somebody configured this app before us; overwriting it would
               destroy a setup we know nothing about.
    ``port``   another value name claims the same port, including the ``*``
               wildcard. Then two apps would fight over one port, and the CDP
               client could end up talking to the wrong application entirely.
    """
    winreg = _winreg()
    args = debug_args(port)
    hklm = _enum_policy(winreg.HKEY_LOCAL_MACHINE)
    hkcu = _enum_policy(winreg.HKEY_CURRENT_USER)
    conflicts = []

    current = hklm.get(exe_name)
    if current is not None and str(current) != args:
        # Our own previous debug port is safe to replace; anything else is a
        # foreign setup we must not overwrite.
        if not str(current).strip().startswith("--remote-debugging-port="):
            conflicts.append({"kind": "value", "hive": "HKLM",
                              "name": exe_name, "value": str(current)})

    # Only worth mentioning when HKLM does not shadow it.
    shadow = hkcu.get(exe_name)
    if current is None and shadow is not None and str(shadow) != args:
        if not str(shadow).strip().startswith("--remote-debugging-port="):
            conflicts.append({"kind": "value", "hive": "HKCU",
                              "name": exe_name, "value": str(shadow)})

    for label, values in (("HKLM", hklm), ("HKCU", hkcu)):
        for name, value in values.items():
            if name.strip().lower() == exe_name.lower():
                continue
            if args in str(value):
                conflicts.append({"kind": "port", "hive": label,
                                  "name": name, "value": str(value)})
    return conflicts


def write_policy(port, exe_name=TARGET_EXE):
    """Write the debug port into the machine-wide WebView2 policy key.

    Callers must run `policy_conflicts()` first; this function does not judge.
    """
    winreg = _winreg()
    args = debug_args(port)
    key = winreg.CreateKeyEx(
        winreg.HKEY_LOCAL_MACHINE, POLICY_KEY, 0, winreg.KEY_SET_VALUE
    )
    try:
        winreg.SetValueEx(key, exe_name, 0, winreg.REG_SZ, args)
    finally:
        key.Close()
    return args


def remove_policy(exe_name=TARGET_EXE):
    """Remove our value; drop the key tree if it becomes empty."""
    winreg = _winreg()
    removed = False
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, POLICY_KEY, 0, winreg.KEY_ALL_ACCESS
        )
    except OSError:
        return False
    try:
        try:
            winreg.DeleteValue(key, exe_name)
            removed = True
        except OSError:
            pass
        try:
            left = winreg.QueryInfoKey(key)[1]
        except OSError:
            left = 1
    finally:
        key.Close()

    if left == 0:
        try:
            winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY)
        except OSError:
            pass
        try:
            winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, POLICY_ROOT)
        except OSError:
            pass
    return removed


# --------------------------------------------------------------- autostart


def autostart_command():
    return subprocess.list2cmdline(command_args("run", "--daemon", background=True))


def read_autostart():
    winreg = _winreg()
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY)
    except OSError:
        return None
    try:
        return winreg.QueryValueEx(key, RUN_VALUE)[0]
    except OSError:
        return None
    finally:
        key.Close()


def enable_autostart():
    winreg = _winreg()
    key = winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    )
    try:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
    finally:
        key.Close()
    return autostart_command()


def disable_autostart():
    winreg = _winreg()
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS
        )
    except OSError:
        return False
    try:
        winreg.DeleteValue(key, RUN_VALUE)
        return True
    except OSError:
        return False
    finally:
        key.Close()


# ---------------------------------------------------------------- watchdog

# A scheduled task is the only thing that can revive a killed process: once the
# process is gone nothing of ours is running any more. The task lives in the
# registry of the system scheduler, so killing the daemon never touches it. It
# must run as the same user, in the same session: it reads that user's data
# directory, so a task running as somebody else would start a second daemon
# against a different profile.
TASK_FOLDER = "\\WuwaTappo"
WATCHDOG_TASK = "KeepAlive"
WATCHDOG_PATH = TASK_FOLDER + "\\" + WATCHDOG_TASK


def watchdog_command():
    return subprocess.list2cmdline(command_args("ensure", background=True))


def register_watchdog(minutes=1, user=None):
    from . import scheduler

    command = command_args("ensure", background=True)
    cwd = Path(command[0]).resolve().parent if frozen() else APP_DIR
    try:
        return scheduler.register(TASK_FOLDER, WATCHDOG_TASK, command[0],
                                  subprocess.list2cmdline(command[1:]), cwd,
                                  max(1, int(minutes or 1)), user)
    except OSError:
        return False


def remove_watchdog():
    from . import scheduler

    return scheduler.remove(TASK_FOLDER, WATCHDOG_TASK)


def read_watchdog():
    from . import scheduler

    return scheduler.read(TASK_FOLDER, WATCHDOG_TASK)


# ---------------------------------------------------------------- processes


_spawn_lock = threading.RLock()


@contextmanager
def _system_dll_search():
    """Do not let a child inherit the bootloader's SetDllDirectory(_MEIPASS).

    This is process state, independent of PATH/env. Restore the exact previous
    directory after CreateProcess/ShellExecute, including when launching fails.
    Serialize launches because changing this directory affects the whole process.
    """
    if not frozen():
        yield
        return
    with _spawn_lock:
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
        api.GetDllDirectoryW.restype = wintypes.DWORD
        api.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
        api.SetDllDirectoryW.restype = wintypes.BOOL
        buffer = ctypes.create_unicode_buffer(32768)
        length = api.GetDllDirectoryW(len(buffer), buffer)
        if length >= len(buffer):
            raise OSError("DLL search directory is too long")
        previous = buffer.value if length else None
        if not api.SetDllDirectoryW(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not api.SetDllDirectoryW(previous):
                raise ctypes.WinError(ctypes.get_last_error())


def popen_external(*args, **kwargs):
    """Create an independent process with system DLL lookup and a clean env."""
    kwargs.setdefault("env", _child_environment())
    with _system_dll_search():
        return subprocess.Popen(*args, **kwargs)


def run_external(*args, **kwargs):
    """Run a system utility without inheriting bundled DLL locations."""
    kwargs.setdefault("env", _child_environment())
    with _system_dll_search():
        return subprocess.run(*args, **kwargs)


def _child_environment(reset=False):
    """Environment for child processes that must not keep this onefile alive.

    Remove extraction paths added by runtime hooks as well as private
    bootloader variables. The DLL search directory itself is sanitized by
    _system_dll_search(); environment cleanup alone cannot reset it.
    """
    environment = dict(os.environ)
    extraction = getattr(sys, "_MEIPASS", None)
    if extraction:
        target = os.path.normcase(os.path.normpath(str(extraction)))
        parts = environment.get("PATH", "").split(os.pathsep)
        environment["PATH"] = os.pathsep.join(
            part for part in parts
            if part and os.path.normcase(os.path.normpath(part)) != target)
    for name in list(environment):
        key = name.upper()
        if key.startswith("_PYI") or key in ("_MEIPASS", "_MEIPASS2"):
            environment.pop(name, None)
    if reset:
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    else:
        environment.pop("PYINSTALLER_RESET_ENVIRONMENT", None)
    return environment


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def _shell_open_app(exe_path):
    """Honor the native app's requested execution level after CreateProcess 740."""
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None

    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    # Keep the operation synchronous through launch, not through app exit.
    # Error UI is handled by our caller; Windows security prompts remain enabled.
    info.fMask = 0x40 | 0x100 | 0x400
    info.lpVerb = "open"
    info.lpFile = str(exe_path)
    info.lpDirectory = str(Path(exe_path).parent)
    info.nShow = 1

    with _spawn_lock, _system_dll_search():
        # ShellExecuteEx has no env argument. Temporarily apply the same cleanup
        # as popen_external, including private PyInstaller variables, then restore.
        environment = _child_environment()
        previous = {name: os.environ.get(name)
                    for name in os.environ.keys() | environment.keys()
                    if os.environ.get(name) != environment.get(name)}
        initialized = False
        try:
            for name in previous:
                if name in environment:
                    os.environ[name] = environment[name]
                else:
                    os.environ.pop(name, None)
            hr = ole.CoInitializeEx(None, 0x2 | 0x4)
            if hr < 0 and hr != -2147417850:  # RPC_E_CHANGED_MODE: already initialized.
                raise OSError("Windows Shell COM 初始化失败：0x%08X" % (hr & 0xffffffff))
            initialized = hr >= 0
            if not shell.ShellExecuteExW(ctypes.byref(info)):
                code = ctypes.get_last_error() or 31
                message = ("已取消桌宠的启动权限确认，请重新运行安装包修复。" if code == 1223 else
                           "Windows Shell 无法启动桌宠 %s：%s" % (exe_path, ctypes.FormatError(code)))
                raise ctypes.WinError(code, message)
        finally:
            try:
                if info.hProcess:
                    kernel.CloseHandle(info.hProcess)
            finally:
                try:
                    if initialized:
                        ole.CoUninitialize()
                finally:
                    for name, value in previous.items():
                        if value is None:
                            os.environ.pop(name, None)
                        else:
                            os.environ[name] = value


def launch_app(exe_path):
    """Start normally; let Windows Shell handle only a required-elevation error."""
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        return popen_external(
            [str(exe_path)],
            creationflags=flags,
            close_fds=True,
            cwd=str(Path(exe_path).parent),
            env=_child_environment(),
        )
    except OSError as exc:
        if getattr(exc, "winerror", None) != 740:
            raise
    # 'open' respects the executable/compatibility settings. Do not force runas
    # on ordinary launches, the Plus daemon or the installation itself.
    return _shell_open_app(exe_path)


def stop_app(hint=""):
    """Close every running copy and wait until no main process remains."""
    exe = find_exe(hint)
    if exe is None:
        return None

    for pid, image in processes():
        # Keep the enumerated image path, so a reused PID cannot target another app.
        # A terminating process can take a moment to release pending I/O.
        for attempt in range(3):
            if kill_pid(pid, expected_exe=image):
                break
            if not pid_alive(pid):
                # It exited on its own between enumeration and the kill
                # request; that is exactly what this function is for.
                break
            if attempt < 2:
                time.sleep(0.5)
        else:
            raise OSError(
                "无法停止 WuwaTappo.exe，进程号 %d。请手动关闭桌宠后重试，"
                "或以管理员身份运行本程序。" % pid)

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and running_pids():
        time.sleep(0.25)

    if running_pids():
        raise OSError("WuwaTappo.exe 仍在运行，已中止重启。")
    return exe


def restart_app(hint=""):
    """Close every running copy and start it again.

    A fresh WebView is the cheapest way to drop injected code: the page comes
    back with nothing in it, so no teardown call is needed.
    """
    exe = stop_app(hint)
    if exe is None:
        return None
    launch_app(exe)
    return exe


def port_available(port, host="127.0.0.1"):
    """True when host:port can be bound; TIME_WAIT is acceptable."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def launch_detached(argv):
    """Start any command with no console and no tie to the caller."""
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    environment = _child_environment(reset=frozen())
    return popen_external(
        [str(a) for a in argv],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True,
        cwd=str(Path(argv[0]).resolve().parent if frozen() else APP_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )


def _process_api():
    """Bind pointer-sized process handles explicitly on Windows x64."""
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.QueryFullProcessImageNameW.restype = wintypes.BOOL
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateProcess.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def _process_error(operation, pid, code):
    return OSError("%s(pid=%d) 失败：%s" % (operation, pid, ctypes.WinError(code)))


def _process_exited(api, handle, milliseconds, pid):
    result = api.WaitForSingleObject(handle, milliseconds)
    if result == 0xFFFFFFFF:
        raise _process_error("WaitForSingleObject", pid, ctypes.get_last_error())
    return result == 0


def kill_pid(pid, timeout=5.0, *, expected_exe=None):
    """Terminate one verified process and wait on the same handle for its exit.

    Never kill the WebView process tree first: a surviving transparent host can
    otherwise cover the desktop with its renderer's error page.
    """
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF or pid == os.getpid():
        raise ValueError("拒绝结束无效 PID 或当前进程。")
    api = _process_api()
    rights = 0x0001 | 0x00100000  # PROCESS_TERMINATE | SYNCHRONIZE
    if expected_exe is not None:
        rights |= 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
    handle = api.OpenProcess(rights, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # The process vanished between enumeration and open.
            return True
        raise _process_error("OpenProcess", pid, error)
    try:
        if _process_exited(api, handle, 0, pid):
            return True
        if expected_exe is not None:
            image = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(image))
            if not api.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
                raise _process_error("QueryFullProcessImageNameW", pid, ctypes.get_last_error())
            actual = os.path.normcase(os.path.normpath(image.value))
            expected = os.path.normcase(os.path.normpath(str(expected_exe)))
            if actual != expected:
                raise OSError("进程 %d 的程序路径已变化，已中止关闭操作。" % pid)
        if not api.TerminateProcess(handle, 0):
            error = ctypes.get_last_error()
            if _process_exited(api, handle, 0, pid):
                return True
            raise _process_error("TerminateProcess", pid, error)
        milliseconds = max(0, min(int(timeout * 1000), 0xFFFFFFFE))
        return _process_exited(api, handle, milliseconds, pid)
    finally:
        api.CloseHandle(handle)


def pid_alive(pid):
    """Failed access is unknown/alive; only confirmed exit means gone."""
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        return False
    api = _process_api()
    handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return ctypes.get_last_error() != 87
    try:
        return api.WaitForSingleObject(handle, 0) != 0
    finally:
        api.CloseHandle(handle)
