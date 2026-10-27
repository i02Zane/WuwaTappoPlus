"""Open a terminal only for interactive use of the packaged executable."""

import ctypes
import sys
import msvcrt
from ctypes import wintypes


def open_console(force_new=False):
    """Reuse the caller's console, or create one for a double-click launch.

    The windowless bootloader keeps registry and watchdog invocations silent.
    Only foreground management commands call this function.
    """
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetConsoleWindow.restype = wintypes.HWND
    kernel.AttachConsole.argtypes = [wintypes.DWORD]
    created = False
    if force_new:
        # UAC actions use their own terminal so the parent menu cannot consume
        # their keyboard input or overwrite their progress output.
        kernel.FreeConsole()
        if not kernel.AllocConsole():
            raise ctypes.WinError(ctypes.get_last_error())
        created = True
    elif not kernel.GetConsoleWindow():
        if not kernel.AttachConsole(0xFFFFFFFF) and not kernel.AllocConsole():
            raise ctypes.WinError(ctypes.get_last_error())
    kernel.SetConsoleCP(65001)
    kernel.SetConsoleOutputCP(65001)
    sys.stdin = open("CONIN$", "r", encoding="utf-8")
    sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
    sys.stderr = sys.stdout
    kernel.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
    for identifier, stream in ((0xFFFFFFF6, sys.stdin), (0xFFFFFFF5, sys.stdout),
                               (0xFFFFFFF4, sys.stderr)):
        if not kernel.SetStdHandle(identifier, msvcrt.get_osfhandle(stream.fileno())):
            raise ctypes.WinError(ctypes.get_last_error())
    return created


def console_info():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetConsoleWindow.restype = wintypes.HWND
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    mode = wintypes.DWORD()
    console = kernel.GetConsoleMode(msvcrt.get_osfhandle(sys.stdout.fileno()), ctypes.byref(mode))
    return {"console": bool(console), "window": bool(kernel.GetConsoleWindow()),
            "stdin_tty": bool(sys.stdin and sys.stdin.isatty()),
            "stdout_tty": bool(sys.stdout and sys.stdout.isatty())}
