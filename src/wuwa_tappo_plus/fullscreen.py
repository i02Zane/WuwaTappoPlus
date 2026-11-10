"""Detect a foreground window whose client area fills its monitor.

The official app compares the window frame, so a windowed game whose client
area covers the monitor (title bar above the screen, or a borderless window)
is not counted as fullscreen. Plus uses this client-area check to drive the native game-mode setting.
API failures return None so they cannot be mistaken for leaving fullscreen.
"""

import ctypes
import os
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GA_ROOT = 2
MONITOR_DEFAULTTONEAREST = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
EXCLUDED_PROCESSES = {
    "wuwatappo.exe", "wuwatappoplus.exe", "explorer.exe", "textinputhost.exe",
    "shellexperiencehost.exe", "startmenuexperiencehost.exe", "searchhost.exe",
    "lockapp.exe",
}
EXCLUDED_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def client_covers_monitor(client, monitor, tolerance=2):
    """True when a (left, top, right, bottom) client rect covers the monitor."""
    return (client[0] <= monitor[0] + tolerance and
            client[1] <= monitor[1] + tolerance and
            client[2] >= monitor[2] - tolerance and
            client[3] >= monitor[3] - tolerance)


def _process_name(pid):
    if not pid:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
    finally:
        kernel32.CloseHandle(handle)
    return ""


def foreground_client_covers_monitor(tolerance=2):
    """Check the foreground window's client area against its monitor."""
    return foreground_window(tolerance)[0]


def foreground_window(tolerance=2):
    """(fullscreen, process name) of the foreground window.

    The name is returned even when the window does not count as fullscreen, so
    callers can also match a whitelisted program. None means the sample failed.
    """
    try:
        return window_state(user32.GetForegroundWindow(), tolerance)
    except (OSError, ValueError):
        return None, ""


def window_client_covers_monitor(hwnd, tolerance=2):
    """Check one window's client area against the monitor it belongs to."""
    return window_state(hwnd, tolerance)[0]


def window_state(hwnd, tolerance=2):
    """(fullscreen, process name) for one window; (None, "") when unknown."""
    try:
        if not hwnd:
            return None, ""
        hwnd = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return False, ""
        class_name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = _process_name(pid.value)
        if not name:
            return None, ""
        if class_name.value in EXCLUDED_CLASSES or name.lower() in EXCLUDED_PROCESSES:
            return False, name
        client = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(client)):
            return None, ""
        if client.right <= 0 or client.bottom <= 0:
            return False, name
        origin = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None, ""
        monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return None, ""
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None, ""
        client_rect = (origin.x, origin.y,
                       origin.x + client.right, origin.y + client.bottom)
        monitor_rect = (info.rcMonitor.left, info.rcMonitor.top,
                        info.rcMonitor.right, info.rcMonitor.bottom)
        return client_covers_monitor(client_rect, monitor_rect, tolerance), name
    except (OSError, ValueError):
        return None, ""
