"""Scheduler errors and XML contracts without registering Windows tasks."""

import ctypes
import unittest
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

from wuwa_tappo_plus import scheduler, win


class SchedulerTests(unittest.TestCase):
    def service(self):
        service = MagicMock()
        service.__enter__.return_value = service
        scope = patch.object(scheduler, "service", return_value=service)
        scope.start()
        self.addCleanup(scope.stop)
        return service

    def test_missing_task_returns_none_without_retry_or_shell(self):
        service = self.service()
        service.call.side_effect = scheduler.SchedulerError(0x80070002, "missing")
        with patch.object(win, "run_external") as shell:
            self.assertIsNone(win.read_watchdog())
        service.call.assert_called_once()
        shell.assert_not_called()

    def test_access_denied_is_not_reported_as_a_missing_task(self):
        service = self.service()
        service.call.side_effect = scheduler.SchedulerError(0x80070005, "denied")
        with self.assertRaises(OSError):
            win.read_watchdog()

    def test_removing_absent_task_succeeds_but_denied_deletion_fails(self):
        service = self.service()
        service.call.side_effect = scheduler.SchedulerError(0x80070003, "missing folder")
        self.assertTrue(win.remove_watchdog())
        service.call.side_effect = scheduler.SchedulerError(0x80070005, "denied")
        with self.assertRaises(OSError):
            win.remove_watchdog()

    def test_xml_preserves_quoted_paths_and_original_user_without_password(self):
        xml = scheduler.task_xml('C:/Users/A & B/Plus.exe', 'ensure --test="a&b"',
                                 'C:/Users/A & B', 3, user='DOMAIN\\original')
        tree = ET.fromstring(xml)
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(tree.findtext('.//t:Command', namespaces=ns), 'C:/Users/A & B/Plus.exe')
        self.assertEqual(tree.findtext('.//t:UserId', namespaces=ns), 'DOMAIN\\original')
        self.assertEqual(tree.findtext('.//t:RunLevel', namespaces=ns), 'LeastPrivilege')
        self.assertEqual(tree.findtext('.//t:LogonType', namespaces=ns), 'InteractiveToken')
        self.assertEqual(tree.findtext('.//t:Interval', namespaces=ns), 'PT3M')
        self.assertNotIn('Password', xml)

    def test_com_variant_matches_windows_abi(self):
        self.assertEqual(ctypes.sizeof(scheduler.Variant), 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)

    def test_registration_is_scoped_to_the_existing_plus_task(self):
        with patch.object(scheduler, "register", return_value=True) as register, \
                patch.object(win, "command_args", return_value=['C:/Plus/App.exe', 'ensure']), \
                patch.object(win, "frozen", return_value=True):
            self.assertTrue(win.register_watchdog(2, user='DOMAIN\\original'))
        register.assert_called_once_with('\\WuwaTappo', 'KeepAlive', 'C:/Plus/App.exe',
                                         'ensure', unittest.mock.ANY, 2, 'DOMAIN\\original')


if __name__ == "__main__":
    unittest.main()
