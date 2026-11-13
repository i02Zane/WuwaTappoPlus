"""Native app elevation requests, with every launch and UAC operation simulated."""

import ctypes
import os
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from wuwa_tappo_plus import win


class AppLaunchTests(unittest.TestCase):
    exe = Path(r'C:\Pet Folder\桌宠\WuwaTappo.exe')

    def test_only_error_740_uses_shell_fallback(self):
        for code in (740, 5, 2):
            with self.subTest(code=code), \
                    patch.object(win, 'popen_external', side_effect=ctypes.WinError(code)) as direct, \
                    patch.object(win, '_shell_open_app') as shell:
                if code == 740:
                    win.launch_app(self.exe)
                    shell.assert_called_once_with(self.exe)
                else:
                    with self.assertRaises(OSError) as failure:
                        win.launch_app(self.exe)
                    self.assertEqual(failure.exception.winerror, code)
                    shell.assert_not_called()
                direct.assert_called_once()

    def shell_context(self, stack):
        shell, kernel, ole = Mock(), Mock(), Mock()
        libraries = {'shell32': shell, 'kernel32': kernel, 'ole32': ole}
        stack.enter_context(patch.object(win.ctypes, 'WinDLL', side_effect=lambda name, **kw: libraries[name]))
        stack.enter_context(patch.object(win, 'frozen', return_value=True))
        stack.enter_context(patch.object(win.sys, '_MEIPASS', r'C:\Plus\_internal', create=True))
        original = {'PATH': r'C:\Plus\_internal;C:\Windows\System32',
                    '_PYI_APPLICATION_HOME_DIR': r'C:\Plus\_internal',
                    '_MEIPASS2': 'old', 'PYINSTALLER_RESET_ENVIRONMENT': '1',
                    'WUWA_TEST_SENTINEL': 'keep'}
        stack.enter_context(patch.dict(os.environ, original))
        directory = {'value': r'C:\Plus\_internal'}

        def get_directory(size, buffer):
            buffer.value = directory['value'] or ''
            return len(buffer.value)

        def set_directory(value):
            directory['value'] = value
            return True

        kernel.GetDllDirectoryW.side_effect = get_directory
        kernel.SetDllDirectoryW.side_effect = set_directory
        ole.CoInitializeEx.return_value = 0

        def check_environment():
            self.assertIsNone(directory['value'])
            self.assertEqual(os.environ['PATH'], r'C:\Windows\System32')
            self.assertNotIn('_PYI_APPLICATION_HOME_DIR', os.environ)
            self.assertNotIn('_MEIPASS2', os.environ)
            self.assertNotIn('PYINSTALLER_RESET_ENVIRONMENT', os.environ)
            self.assertEqual(os.environ['WUWA_TEST_SENTINEL'], 'keep')

        return shell, kernel, ole, directory, original, check_environment

    def test_shell_honors_target_settings_and_releases_native_resources(self):
        for hr, handle in ((0, 0x1234567890), (-2147417850, None)):
            with self.subTest(com=hr), ExitStack() as stack:
                shell, kernel, ole, directory, original, check = self.shell_context(stack)
                ole.CoInitializeEx.return_value = hr

                def execute(pointer):
                    check()
                    info = ctypes.cast(pointer, ctypes.POINTER(win._ShellExecuteInfo)).contents
                    self.assertEqual(ctypes.sizeof(info), 112)
                    self.assertEqual(info.cbSize, ctypes.sizeof(info))
                    self.assertEqual(info.lpVerb, 'open')
                    self.assertEqual(info.lpFile, str(self.exe))
                    self.assertEqual(info.lpDirectory, str(self.exe.parent))
                    self.assertIsNone(info.lpParameters)
                    self.assertEqual(info.fMask, 0x540)
                    info.hProcess = handle
                    return True

                shell.ShellExecuteExW.side_effect = execute
                win._shell_open_app(self.exe)
                self.assertEqual(directory['value'], r'C:\Plus\_internal')
                self.assertTrue(all(os.environ[key] == value for key, value in original.items()))
                ole.CoInitializeEx.assert_called_once_with(None, 6)
                if handle:
                    kernel.CloseHandle.assert_called_once_with(handle)
                else:
                    kernel.CloseHandle.assert_not_called()
                self.assertEqual(ole.CoUninitialize.call_count, 1 if hr == 0 else 0)

    def test_cancel_or_shell_failure_preserves_error_and_parent_environment(self):
        for code in (1223, 5, 2):
            with self.subTest(code=code), ExitStack() as stack:
                shell, kernel, ole, directory, original, check = self.shell_context(stack)
                stack.enter_context(patch.object(win.ctypes, 'get_last_error', return_value=code))

                def fail(pointer):
                    check()
                    return False

                shell.ShellExecuteExW.side_effect = fail
                with self.assertRaises(OSError) as failure:
                    win._shell_open_app(self.exe)
                self.assertEqual(failure.exception.winerror, code)
                if code == 1223:
                    self.assertIn('已取消', str(failure.exception))
                self.assertEqual(directory['value'], r'C:\Plus\_internal')
                self.assertTrue(all(os.environ[key] == value for key, value in original.items()))
                ole.CoUninitialize.assert_called_once()
                kernel.CloseHandle.assert_not_called()
