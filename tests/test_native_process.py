"""Native process termination with simulated handles, errors and exit races."""

import ctypes
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, call, patch

from wuwa_tappo_plus import win


class NativeProcessTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.handle = 0x1234567890
        self.api.OpenProcess.return_value = self.handle
        self.api.TerminateProcess.return_value = True
        self.api.WaitForSingleObject.side_effect = [0x102, 0]
        self.factory = patch.object(win, '_process_api', return_value=self.api)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    def image(self, text):
        def query(handle, flags, buffer, size):
            buffer.value = text
            return True
        self.api.QueryFullProcessImageNameW.side_effect = query

    def test_success_waits_for_the_same_pointer_sized_handle_without_a_shell(self):
        self.image(r'D:\Pet\WuwaTappo.exe')
        with patch.object(win, 'run_external') as shell:
            self.assertTrue(win.kill_pid(42, expected_exe=r'D:\Pet\WuwaTappo.exe'))
        shell.assert_not_called()
        self.api.TerminateProcess.assert_called_once_with(self.handle, 0)
        self.assertEqual(self.api.WaitForSingleObject.call_args_list,
                         [call(self.handle, 0), call(self.handle, 5000)])
        self.api.CloseHandle.assert_called_once_with(self.handle)

    def test_reused_pid_with_another_image_is_never_terminated(self):
        self.image(r'C:\Other\Other.exe')
        with self.assertRaisesRegex(OSError, '程序路径已变化'):
            win.kill_pid(42, expected_exe=r'D:\Pet\WuwaTappo.exe')
        self.api.TerminateProcess.assert_not_called()
        self.api.CloseHandle.assert_called_once_with(self.handle)

    def test_missing_process_is_already_stopped(self):
        self.api.OpenProcess.return_value = None
        with patch.object(win.ctypes, 'get_last_error', return_value=87):
            self.assertTrue(win.kill_pid(42))
        self.api.TerminateProcess.assert_not_called()
        self.api.CloseHandle.assert_not_called()

    def test_access_denied_is_reported_with_operation_and_pid(self):
        self.api.OpenProcess.return_value = None
        with patch.object(win.ctypes, 'get_last_error', return_value=5):
            with self.assertRaisesRegex(OSError, r'OpenProcess\(pid=42\).*5'):
                win.kill_pid(42)
        self.api.TerminateProcess.assert_not_called()

    def test_a_process_that_already_exited_needs_no_termination(self):
        self.api.WaitForSingleObject.side_effect = [0]
        self.assertTrue(win.kill_pid(42))
        self.api.TerminateProcess.assert_not_called()
        self.api.CloseHandle.assert_called_once_with(self.handle)

    def test_exit_racing_with_termination_is_success(self):
        self.api.TerminateProcess.return_value = False
        with patch.object(win.ctypes, 'get_last_error', return_value=5):
            self.assertTrue(win.kill_pid(42))
        self.api.CloseHandle.assert_called_once_with(self.handle)

    def test_failed_termination_of_a_live_process_keeps_the_actual_error(self):
        self.api.TerminateProcess.return_value = False
        self.api.WaitForSingleObject.side_effect = [0x102, 0x102]
        with patch.object(win.ctypes, 'get_last_error', return_value=5):
            with self.assertRaisesRegex(OSError, r'TerminateProcess\(pid=42\).*5'):
                win.kill_pid(42)
        self.api.CloseHandle.assert_called_once_with(self.handle)

    def test_successful_request_with_exit_timeout_is_not_reported_as_gone(self):
        self.api.WaitForSingleObject.side_effect = [0x102, 0x102]
        self.assertFalse(win.kill_pid(42, timeout=0.5))
        self.assertEqual(self.api.WaitForSingleObject.call_args, call(self.handle, 500))
        self.api.CloseHandle.assert_called_once_with(self.handle)

    def test_wait_failure_aborts_without_terminating(self):
        self.api.WaitForSingleObject.side_effect = [0xFFFFFFFF]
        with patch.object(win.ctypes, 'get_last_error', return_value=6):
            with self.assertRaisesRegex(OSError, 'WaitForSingleObject'):
                win.kill_pid(42)
        self.api.TerminateProcess.assert_not_called()
        self.api.CloseHandle.assert_called_once_with(self.handle)

    def test_image_query_failure_aborts_without_terminating(self):
        self.api.QueryFullProcessImageNameW.return_value = False
        with patch.object(win.ctypes, 'get_last_error', return_value=5):
            with self.assertRaisesRegex(OSError, 'QueryFullProcessImageNameW'):
                win.kill_pid(42, expected_exe=r'D:\Pet\WuwaTappo.exe')
        self.api.TerminateProcess.assert_not_called()
        self.api.CloseHandle.assert_called_once_with(self.handle)

    def test_invalid_pid_and_self_termination_are_rejected_before_open(self):
        for pid in (0, -1, True, '42', 0x100000000, os.getpid()):
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                win.kill_pid(pid)
        self.api.OpenProcess.assert_not_called()

    def test_unreadable_process_cannot_be_mistaken_for_confirmed_exit(self):
        self.api.OpenProcess.return_value = None
        with patch.object(win.ctypes, 'get_last_error', return_value=5):
            self.assertTrue(win.pid_alive(42))
        with patch.object(win.ctypes, 'get_last_error', return_value=87):
            self.assertFalse(win.pid_alive(42))

    def test_pid_liveness_uses_the_process_signal_and_closes_handle(self):
        self.api.WaitForSingleObject.side_effect = [0, 0x102, 0xFFFFFFFF]
        self.assertFalse(win.pid_alive(42))
        self.assertTrue(win.pid_alive(42))
        self.assertTrue(win.pid_alive(42))
        self.assertEqual(self.api.CloseHandle.call_count, 3)


class PermissionPreflightTests(unittest.TestCase):
    def test_actual_tokens_and_stop_rights_are_reported_without_termination(self):
        for elevated, integrity, denied in ((False, 0x2000, False), (True, 0x3000, False),
                                           (True, 0x3000, True), (False, 0x1000, False)):
            with self.subTest(elevated=elevated, integrity=integrity, denied=denied):
                api, security = Mock(), Mock()
                api.OpenProcess.side_effect = [None, 0x1234567890] if denied else [0x1234567890]
                api.WaitForSingleObject.return_value = 0x102
                def image(handle, flags, buffer, size):
                    buffer.value = r'D:\Pet\WuwaTappo.exe'
                    return True
                api.QueryFullProcessImageNameW.side_effect = image
                def token(handle, rights, output):
                    output._obj.value = 0x2345678901
                    return True
                security.OpenProcessToken.side_effect = token
                def information(handle, kind, buffer, size, length):
                    if kind == 20:
                        buffer._obj.value = int(elevated)
                        return True
                    length._obj.value = ctypes.sizeof(win._SidAndAttributes)
                    if buffer is None:
                        ctypes.set_last_error(122)
                        return False
                    ctypes.cast(buffer, ctypes.POINTER(win._SidAndAttributes)).contents.sid = 123
                    return True
                security.GetTokenInformation.side_effect = information
                count = ctypes.c_ubyte(1)
                rid = win.wintypes.DWORD(integrity)
                security.GetSidSubAuthorityCount.return_value = ctypes.pointer(count)
                security.GetSidSubAuthority.return_value = ctypes.pointer(rid)
                def open_process(*args):
                    ctypes.set_last_error(5)
                    return next(handles)
                handles = iter(api.OpenProcess.side_effect)
                api.OpenProcess.side_effect = open_process
                messages = []
                with patch.object(win, '_process_api', return_value=api), \
                     patch.object(win, '_token_api', return_value=security), \
                     patch.object(win, 'processes', return_value=[(42, Path(r'D:\Pet\WuwaTappo.exe'))]):
                    if denied:
                        with self.assertRaisesRegex(OSError, 'PROCESS_TERMINATE.*42.*5'):
                            win.check_app_permissions(messages.append)
                    else:
                        self.assertTrue(win.check_app_permissions(messages.append))
                self.assertIn('已提升' if elevated else '未提升', messages[0])
                self.assertIn({0x1000: '低', 0x2000: '中', 0x3000: '高'}[integrity], messages[0])
                api.TerminateProcess.assert_not_called()
                self.assertEqual(api.CloseHandle.call_count, 2)
                self.assertEqual(api.CloseHandle.call_args, call(0x1234567890))

    def test_unreadable_reused_and_exited_processes_are_distinct(self):
        for case in ('denied', 'token', 'information', 'reused', 'gone'):
            with self.subTest(case=case):
                api, security = Mock(), Mock()
                api.OpenProcess.return_value = None if case in ('denied', 'gone') else 123
                api.WaitForSingleObject.return_value = 0x102
                def image(handle, flags, buffer, size):
                    buffer.value = 'Other.exe' if case == 'reused' else 'WuwaTappo.exe'
                    return True
                api.QueryFullProcessImageNameW.side_effect = image
                security.OpenProcessToken.return_value = False
                if case == 'information':
                    def token(handle, rights, output):
                        output._obj.value = 456
                        return True
                    security.OpenProcessToken.side_effect = token
                    security.GetTokenInformation.return_value = False
                with patch.object(win, '_process_api', return_value=api), \
                     patch.object(win, '_token_api', return_value=security), \
                     patch.object(win.ctypes, 'get_last_error', return_value=87 if case == 'gone' else 5):
                    if case == 'gone':
                        self.assertIsNone(win.process_permissions(42, 'WuwaTappo.exe'))
                    else:
                        with self.assertRaises(OSError):
                            win.process_permissions(42, 'WuwaTappo.exe')
                api.TerminateProcess.assert_not_called()
                self.assertEqual(api.CloseHandle.call_count, 2 if case == 'information' else int(case in ('token', 'reused')))

    def test_enumeration_keeps_x64_handles_and_never_hides_access_failure(self):
        for error in (0, 5, 87):
            with self.subTest(error=error):
                api = Mock()
                api.CreateToolhelp32Snapshot.return_value = 0x1234567890
                def first(snapshot, entry):
                    entry._obj.th32ProcessID = 42
                    entry._obj.szExeFile = 'WuwaTappo.exe'
                    return True
                def next_process(*args):
                    ctypes.set_last_error(18)
                    return False
                def open_process(*args):
                    ctypes.set_last_error(error)
                    return None if error else 0x2345678901
                def image(handle, flags, buffer, size):
                    buffer.value = r'D:\Pet\WuwaTappo.exe'
                    return True
                api.Process32FirstW.side_effect = first
                api.Process32NextW.side_effect = next_process
                api.OpenProcess.side_effect = open_process
                api.QueryFullProcessImageNameW.side_effect = image
                with patch.object(win, '_process_api', return_value=api):
                    if error == 5:
                        with self.assertRaisesRegex(OSError, 'OpenProcess.*42.*5'):
                            win.processes()
                    else:
                        self.assertEqual(win.processes(), [] if error else [(42, Path(r'D:\Pet\WuwaTappo.exe'))])
                self.assertIs(api.CreateToolhelp32Snapshot.restype, win.wintypes.HANDLE)
                self.assertEqual(api.CloseHandle.call_args, call(0x1234567890))

        api = Mock()
        api.CreateToolhelp32Snapshot.return_value = ctypes.c_void_p(-1).value
        with patch.object(win, '_process_api', return_value=api), \
             patch.object(win.ctypes, 'get_last_error', return_value=5):
            with self.assertRaisesRegex(OSError, 'CreateToolhelp32Snapshot.*5'):
                win.processes()
        api.CloseHandle.assert_not_called()

        with patch.object(win, 'processes', return_value=[]), \
             patch.object(win, 'process_permissions') as inspect:
            self.assertFalse(win.check_app_permissions())
        inspect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
