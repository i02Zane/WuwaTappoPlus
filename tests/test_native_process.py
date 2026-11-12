"""Native process termination with simulated handles, errors and exit races."""

import os
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


if __name__ == '__main__':
    unittest.main()
