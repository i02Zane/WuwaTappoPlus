"""Windows integration: WebView2 debug policy, autostart, discovery, processes.

The debug port is enabled through the official WebView2 policy key, which the
runtime appends to whatever AdditionalBrowserArguments the host app already
set. That is why it works despite Tauri hard-coding its own arguments.
"""

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
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


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def relaunch_elevated(argv):
    """Re-run this process with a UAC prompt. Returns True if launched."""
    command = command_args("--data-dir", str(ROOT), *argv)
    params = subprocess.list2cmdline(command[1:])
    previous_reset = os.environ.get("PYINSTALLER_RESET_ENVIRONMENT")
    if frozen():
        os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
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
        return "%s  (packaged)" % sys.executable
    bundled = bundled_python()
    if bundled:
        return "%s  (bundled)" % bundled
    return "%s  (system fallback)" % sys.executable


def command_args(*args, background=False):
    """Build the same application command in source and frozen distributions."""
    if frozen():
        return [sys.executable, *map(str, args)]
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
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
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

    found = []
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID:
        return found
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == TARGET_EXE.lower():
                handle = k32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, entry.th32ProcessID
                )
                if handle:
                    try:
                        buf = ctypes.create_unicode_buffer(32768)
                        size = wintypes.DWORD(32768)
                        if k32.QueryFullProcessImageNameW(
                            handle, 0, buf, ctypes.byref(size)
                        ):
                            found.append((entry.th32ProcessID, Path(buf.value)))
                    finally:
                        k32.CloseHandle(handle)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return found


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
        conflicts.append({"kind": "value", "hive": "HKLM",
                          "name": exe_name, "value": str(current)})

    # Only worth mentioning when HKLM does not shadow it.
    shadow = hkcu.get(exe_name)
    if current is None and shadow is not None and str(shadow) != args:
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
# must run in the user's own session - a service in session 0 has no access to
# the taskbar button, and the blink would silently do nothing.
TASK_FOLDER = "\\WuwaTappo"
# The ScheduledTasks cmdlets only match a folder when the path ends in a
# backslash; schtasks wants the joined form instead.
TASK_FOLDER_PS = TASK_FOLDER + "\\"
WATCHDOG_TASK = "KeepAlive"
WATCHDOG_PATH = TASK_FOLDER + "\\" + WATCHDOG_TASK
SCHTASKS = 0x08000000  # CREATE_NO_WINDOW


def watchdog_command():
    """What the scheduled task runs: a no-op unless the daemon is missing."""
    return subprocess.list2cmdline(command_args("ensure", background=True))


def _schtasks(*args, timeout=25.0):
    try:
        return subprocess.run(
            ["schtasks"] + [str(a) for a in args],
            capture_output=True,
            timeout=timeout,
            creationflags=SCHTASKS,
        )
    except Exception:
        return None


def _powershell(script, timeout=45.0):
    try:
        return subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", script,
            ],
            capture_output=True,
            timeout=timeout,
            creationflags=SCHTASKS,
        )
    except Exception:
        return None


def _ps(value):
    """Single-quote a value for PowerShell; paths never contain quotes, but be safe."""
    return "'" + str(value).replace("'", "''") + "'"


def _register_watchdog_ps(minutes):
    """Fallback used when schtasks.exe is missing or blocked by policy."""
    script = (
        "$ErrorActionPreference='Stop';"
        "$a=New-ScheduledTaskAction -Execute %s -Argument %s -WorkingDirectory %s;"
        "$t=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(%d) "
        "-RepetitionInterval (New-TimeSpan -Minutes %d) "
        # [TimeSpan]::MaxValue is rejected by the task schema (out of range), so
        # use ten years: long enough to read as "forever" for this purpose.
        "-RepetitionDuration ([TimeSpan]::FromDays(3650));"
        "$p=New-ScheduledTaskPrincipal -UserId \"$env:USERDOMAIN\\$env:USERNAME\" "
        "-LogonType Interactive -RunLevel Highest;"
        "Register-ScheduledTask -TaskName %s -TaskPath %s -Action $a -Trigger $t "
        "-Principal $p -Force | Out-Null"
    ) % (
        _ps(command_args("ensure", background=True)[0]),
        _ps(subprocess.list2cmdline(command_args("ensure", background=True)[1:])),
        _ps(APP_DIR),
        minutes,
        minutes,
        _ps(WATCHDOG_TASK),
        _ps(TASK_FOLDER_PS),
    )
    res = _powershell(script)
    return bool(res) and res.returncode == 0


def register_watchdog(minutes=1):
    """(Re)create the keep-alive task. Needs an elevated caller for /RL HIGHEST."""
    minutes = max(1, int(minutes or 1))
    res = _schtasks(
        "/Create",
        "/TN", WATCHDOG_PATH,
        "/SC", "MINUTE",
        "/MO", str(minutes),
        "/RL", "HIGHEST",
        "/F",
        "/TR", watchdog_command(),
    )
    if res is not None and res.returncode == 0:
        return True
    return _register_watchdog_ps(minutes)


def remove_watchdog():
    """Delete the keep-alive task. True when it is gone afterwards."""
    if read_watchdog() is None:
        return True
    res = _schtasks("/Delete", "/TN", WATCHDOG_PATH, "/F")
    if res is not None and res.returncode == 0:
        return True
    script = (
        "$ErrorActionPreference='Stop';"
        "Unregister-ScheduledTask -TaskName %s -TaskPath %s -Confirm:$false"
    ) % (_ps(WATCHDOG_TASK), _ps(TASK_FOLDER_PS))
    res = _powershell(script)
    return bool(res) and res.returncode == 0


def read_watchdog():
    """Raw `schtasks /query` fields, or None when the task is not registered."""
    res = _schtasks("/Query", "/TN", WATCHDOG_PATH, "/FO", "LIST")
    if res is not None and res.returncode == 0:
        fields = {}
        for line in res.stdout.decode("mbcs", "replace").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key.strip()] = value.strip()
        if fields:
            return fields

    script = (
        "$t=Get-ScheduledTask -TaskName %s -TaskPath %s "
        "-ErrorAction SilentlyContinue;"
        "if ($t) { [Console]::Out.Write([string]$t.State) }"
    ) % (_ps(WATCHDOG_TASK), _ps(TASK_FOLDER_PS))
    # The task store lags a second or two behind a fresh registration, so ask
    # twice before reporting the task as missing.
    for attempt in range(2):
        res = _powershell(script)
        if res is not None and res.returncode == 0:
            label = res.stdout.decode("mbcs", "replace").strip()
            if label:
                return {"state": label}
        if attempt == 0:
            import time as _time

            _time.sleep(1.5)
    return None


# ---------------------------------------------------------------- processes


def launch_app(exe_path):
    """Start the app detached from this process."""
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        [str(exe_path)],
        creationflags=flags,
        close_fds=True,
        cwd=str(Path(exe_path).parent),
    )


def restart_app(hint=""):
    """Close every running copy and start it again.

    A fresh WebView is the cheapest way to drop injected code: the page comes
    back with nothing in it, so no teardown call is needed.
    """
    import time

    exe = find_exe(hint)
    if exe is None:
        return None

    for pid in running_pids():
        if not kill_pid(pid):
            raise OSError("could not stop WuwaTappo.exe pid %d" % pid)

    deadline = time.time() + 8.0
    while time.time() < deadline and running_pids():
        time.sleep(0.25)

    if running_pids():
        raise OSError("WuwaTappo.exe is still running; restart aborted")

    launch_app(exe)
    return exe


def launch_detached(argv):
    """Start any command with no console and no tie to the caller."""
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    environment = dict(os.environ)
    if frozen():
        # An independent onefile instance must own its extracted resources;
        # its launching menu/watchdog may exit before this daemon does.
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return subprocess.Popen(
        [str(a) for a in argv],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True,
        cwd=str(APP_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )


def kill_pid(pid, timeout=5.0):
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=0x08000000,
            timeout=timeout,
        )
        return result.returncode == 0 and not pid_alive(pid)
    except Exception:
        return False


def pid_alive(pid):
    """True if the pid exists and has not exited."""
    if not pid:
        return False
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)
