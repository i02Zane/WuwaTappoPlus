"""Remember which monitor the pet is on and put it back after a restart.

The app's own display switch is wired to an F8 keydown listener in its page, so
the daemon only has to dispatch that event; the app then resizes the window and
re-lays out the pet. Nothing here touches the app's files or memory, and a
missing monitor is never fatal: the pet stays where it is.
"""

import ctypes
import json
import time
from ctypes import wintypes
from pathlib import Path

from . import cdp
from .config import ROOT

RECORD_PATH = ROOT / "screen.json"
PET_CLASS = "Tauri Window"
PET_TITLE = "Wuwa Tappo"

_MONITORINFOF_PRIMARY = 0x1
_MONITOR_DEFAULTTONEAREST = 0x2

_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, ctypes.c_void_p, wintypes.HDC,
    ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


_user32 = None


def _api():
    global _user32
    if _user32 is None:
        # Match the frozen build's DPI awareness so a source run and the EXE see
        # the same physical monitor rectangles. Best effort: a manifest may
        # already have set it, which makes this fail harmlessly.
        try:
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        except (OSError, AttributeError):
            pass
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.EnumDisplayMonitors.argtypes = [
            wintypes.HWND, wintypes.HDC, _MONITORENUMPROC, wintypes.LPARAM]
        user32.EnumDisplayDevicesW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(_DISPLAY_DEVICEW), wintypes.DWORD]
        user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFOEXW)]
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        _user32 = user32
    return _user32


def _monitor_identity(device):
    """(friendly name, hardware id) for a display output, both possibly "".

    The friendly name is often just "Generic PnP Monitor", so the hardware id
    (e.g. "MONITOR\\RVM2740") is the more useful half of the pair.
    """
    info = _DISPLAY_DEVICEW()
    info.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
    if not _api().EnumDisplayDevicesW(device, 0, ctypes.byref(info), 0):
        return "", ""
    parts = info.DeviceID.split("\\")
    hardware = "\\".join(parts[:2]) if len(parts) >= 2 else ""
    return info.DeviceString, hardware


def _monitor_dict(info):
    name, hardware = _monitor_identity(info.szDevice)
    return {
        "device": info.szDevice,
        "rect": [info.rcMonitor.left, info.rcMonitor.top,
                 info.rcMonitor.right - info.rcMonitor.left,
                 info.rcMonitor.bottom - info.rcMonitor.top],
        "primary": bool(info.dwFlags & _MONITORINFOF_PRIMARY),
        "name": name,
        "id": hardware,
    }


def _monitor_from_handle(handle):
    info = _MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
    if not _api().GetMonitorInfoW(handle, ctypes.byref(info)):
        return None
    return _monitor_dict(info)


def list_monitors():
    """Every attached display, in the order Windows enumerates them."""
    monitors = []

    def callback(handle, _hdc, _rect, _data):
        monitor = _monitor_from_handle(handle)
        if monitor:
            monitors.append(monitor)
        return True

    _api().EnumDisplayMonitors(None, None, _MONITORENUMPROC(callback), 0)
    return monitors


def pet_window():
    """The pet's main window, identified by its class and product title."""
    found = {}

    def callback(hwnd, _data):
        name = ctypes.create_unicode_buffer(64)
        _api().GetClassNameW(hwnd, name, 64)
        if name.value != PET_CLASS:
            return True
        title = ctypes.create_unicode_buffer(64)
        _api().GetWindowTextW(hwnd, title, 64)
        if title.value != PET_TITLE:
            return True
        found["hwnd"] = hwnd
        return False

    _api().EnumWindows(_WNDENUMPROC(callback), 0)
    return found.get("hwnd")


def monitor_of_window(hwnd):
    if not hwnd:
        return None
    handle = _api().MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
    if not handle:
        return None
    return _monitor_from_handle(handle)


class ScreenKeeper:
    """Follow the pet across monitors and restore it when the app restarts.

    One restore attempt is made per pet window: after that a different monitor
    is treated as the user's own choice and only the record is updated. A
    display being added, removed or rearranged resets that check, and a record
    whose monitor is gone is kept (not overwritten) so it can be restored when
    the monitor comes back.
    """

    def __init__(self, log, path=None, clock=None, settle=2.5):
        self.log = log
        self.path = Path(path) if path is not None else RECORD_PATH
        self.clock = clock or time.monotonic
        self.settle = settle
        self.target = None
        self.restoring = False
        self.attempts = 0
        self.next_check = 0.0
        self.checked_hwnd = None
        self.signature = None
        self.missing = False
        self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and isinstance(data.get("device"), str):
            self.target = data

    def remember(self, monitor):
        self.target = {"device": monitor["device"], "rect": list(monitor["rect"]),
                       "name": monitor.get("name", ""), "id": monitor.get("id", "")}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.target, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as exc:
            self.log.warn("screen: cannot remember the monitor: %s" % exc)

    @staticmethod
    def _same_monitor(recorded, monitor):
        """False when both sides describe the same output but a different monitor."""
        for field in ("id", "name"):
            want = recorded.get(field) or ""
            have = monitor.get(field) or ""
            if want and have and want != have:
                return False
        return True

    def _recorded(self, monitors):
        """The monitor to restore to, or None when it is not in the layout.

        The hardware id follows the monitor itself, so replugging it into another
        output, renaming the output or changing its resolution still matches. Two
        identical models share the id; for those the last output is preferred.
        """
        if not self.target:
            return None
        wanted = (self.target.get("id") or "").strip()
        if wanted:
            same = [m for m in monitors if (m.get("id") or "").strip() == wanted]
            if not same:
                return None
            for field in ("device", "rect"):
                for monitor in same:
                    if monitor.get(field) == self.target.get(field):
                        return monitor
            return same[0]
        # A record without a hardware id (an older file or a virtual display)
        # falls back to the output name, then the geometry.
        for monitor in monitors:
            if monitor["device"] == self.target.get("device"):
                return monitor if self._same_monitor(self.target, monitor) else None
        wanted_rect = self.target.get("rect")
        if isinstance(wanted_rect, list) and len(wanted_rect) == 4:
            for monitor in monitors:
                if monitor["rect"] == wanted_rect:
                    return monitor if self._same_monitor(self.target, monitor) else None
        return None

    def _dispatch(self, session):
        """Ask the app to switch displays the same way its F8 shortcut does."""
        try:
            session.js("window.dispatchEvent(new KeyboardEvent('keydown', {key: 'F8'})); 1")
            return True
        except (cdp.CDPError, OSError, AttributeError) as exc:
            self.log.warn("screen: cannot ask the app to switch displays: %s" % str(exc)[:160])
            return False

    def tick(self, session, enabled=True):
        if not enabled:
            self.restoring = False
            return
        hwnd = pet_window()
        if hwnd is None:
            return
        monitors = list_monitors()
        current = monitor_of_window(hwnd)
        if not monitors or current is None:
            return

        signature = tuple((m["device"], tuple(m["rect"])) for m in monitors)
        if signature != self.signature:
            # A display appeared, vanished or moved: wait for the new layout and
            # re-check instead of treating the change as a user preference.
            self.signature = signature
            self.checked_hwnd = None

        if self.target is None:
            self.remember(current)
            self.checked_hwnd = hwnd
            return

        target = self._recorded(monitors)
        if target is None:
            if not self.missing:
                self.missing = True
                self.log.warn(
                    "screen: remembered monitor %s is not connected; leaving the pet where it is"
                    % self.target.get("device"))
            self.restoring = False
            self.checked_hwnd = None
            return
        self.missing = False

        if self.restoring:
            if self.clock() < self.next_check:
                return
            if current["device"] == target["device"]:
                self.restoring = False
                self.remember(current)
                self.log.info("screen: pet is back on %s" % target["device"])
                return
            if self.attempts >= len(monitors):
                self.restoring = False
                self.log.warn("screen: could not move the pet back to %s; left it on %s"
                              % (target["device"], current["device"]))
                return
            if self._dispatch(session):
                self.attempts += 1
                self.next_check = self.clock() + self.settle
            else:
                self.restoring = False
            return

        if self.checked_hwnd == hwnd:
            # The app has not restarted since the last check, so a different
            # monitor means the user moved it: follow the user.
            if (current["device"] != self.target.get("device")
                    or current["rect"] != self.target.get("rect")):
                self.remember(current)
            return

        self.checked_hwnd = hwnd
        if current["device"] != target["device"]:
            self.restoring = True
            self.attempts = 1
            self.next_check = self.clock() + self.settle
            self.log.info("screen: pet is on %s, moving it to %s"
                          % (current["device"], target["device"]))
            if not self._dispatch(session):
                self.restoring = False
