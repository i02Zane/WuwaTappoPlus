"""Setup transitions preserve user data and separate privileged operations."""

import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from wuwa_tappo_plus import entry, installation, installer, lifecycle, paths, win
from wuwa_tappo_plus.config import Config, DEFAULT_TOML
from wuwa_tappo_plus.operations import OperationResult


class InstallerTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.data = self.root / 'data'
        self.data.mkdir()
        self.program = self.root / 'WuwaTappoPlus'
        self.program.mkdir()
        self.path = self.data / 'config.toml'
        self.path.write_text(DEFAULT_TOML, encoding='utf-8')
        stack.enter_context(patch.object(installer, 'DATA_DIR', self.data))
        stack.enter_context(patch.object(lifecycle, 'CONFIG_PATH', self.path))
        stack.enter_context(patch.object(installation, 'directory', return_value=self.program))
        stack.enter_context(patch.object(installer, 'load', return_value=Config({})))
        self.stop = stack.enter_context(patch.object(installer.daemon_control, 'stop', return_value=OperationResult()))
        self.start = stack.enter_context(patch.object(installer.daemon_control, 'start', return_value=OperationResult()))
        self.find = stack.enter_context(patch.object(win, 'find_exe', return_value=self.root/'WuwaTappo.exe'))
        self.processes = stack.enter_context(patch.object(win, 'processes', return_value=[]))
        self.stop_app = stack.enter_context(patch.object(win, 'stop_app', return_value=self.root/'WuwaTappo.exe'))
        self.launch = stack.enter_context(patch.object(win, 'launch_app'))
        self.conflicts = stack.enter_context(patch.object(win, 'policy_conflicts', return_value=[]))
        self.available = stack.enter_context(patch.object(win, 'port_available', return_value=True))
        self.admin = stack.enter_context(patch.object(win, 'is_admin', return_value=True))
        self.policy = stack.enter_context(patch.object(win, 'write_policy'))
        self.task = stack.enter_context(patch.object(win, 'register_watchdog', return_value=True))

    def test_legacy_upgrade_keeps_all_personal_data_and_cancels_old_cleanup_generation(self):
        exe = self.program/'WuwaTappoPlus.exe'
        exe.write_bytes(b'legacy')
        receipt = self.program/'install.json'
        receipt.write_text('{"generation":"old"}', encoding='utf-8')
        sentinel = self.data/'accessory_overrides.json'
        sentinel.write_bytes(b'personal data')
        installer.prepare()
        self.assertEqual(exe.read_bytes(), b'legacy')
        self.assertEqual(sentinel.read_bytes(), b'personal data')
        self.assertNotEqual(json.loads(receipt.read_text())['generation'], 'old')
        self.assertTrue(json.loads(receipt.read_text())['pending'])
        self.policy.assert_not_called()
        self.task.assert_not_called()

    def test_conflicts_abort_before_stopping_or_adopting_the_old_install(self):
        self.conflicts.return_value = [{'kind': 'value'}]
        with self.assertRaises(OSError):
            installer.prepare()
        self.stop.assert_not_called()
        self.assertFalse((self.program/'install.json').exists())

    def test_unknown_daemon_prevents_replacing_program_files(self):
        failed = OperationResult('failed')
        from wuwa_tappo_plus.operations import StepResult

        failed.steps.append(StepResult('stop_daemon', 'failed', 'lock held'))
        self.stop.return_value = failed
        with self.assertRaises(OSError):
            installer.prepare()
        self.stop_app.assert_not_called()
        self.assertFalse((self.program/'install.json').exists())

    def test_privileged_configure_uses_original_user_for_task_and_never_touches_hkcu(self):
        with patch.object(win, 'enable_autostart') as startup, patch.object(win, 'launch_app') as launch:
            installer.configure('DOMAIN\\original')
        self.task.assert_called_once_with(1, user='DOMAIN\\original')
        startup.assert_not_called()
        launch.assert_not_called()

    def test_cancelled_elevation_cannot_mark_installation_complete(self):
        self.admin.return_value = False
        with self.assertRaises(OSError):
            installer.configure('DOMAIN\\original')
        self.policy.assert_not_called()
        self.task.assert_not_called()

    def test_invalid_config_aborts_before_writing_system_policy(self):
        self.path.write_text('[broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            installer.configure('DOMAIN\\original')
        self.policy.assert_not_called()
        self.task.assert_not_called()

    def test_task_registration_failure_is_not_reported_as_success(self):
        self.task.return_value = False
        with self.assertRaises(OSError):
            installer.configure('DOMAIN\\original')

    def test_linked_data_subdirectory_is_rejected_before_any_operation(self):
        outside = self.root / 'outside'
        outside.mkdir()
        try:
            (self.data/'reports').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('directory link permission unavailable')
        with self.assertRaises(OSError):
            installer.validate_paths()
        self.stop.assert_not_called()

    def test_privileged_cleanup_leaves_files_hkcu_and_native_restart_to_original_user(self):
        with patch.object(lifecycle, 'uninstall', return_value=OperationResult()) as uninstall:
            installer.remove_system()
        uninstall.assert_called_once_with(remove_program=False, manage_autostart=False, restart_app=False)

    def test_native_start_failure_does_not_commit_the_installation(self):
        with patch.object(installer.cdp, 'probe_port', return_value=None), \
                patch.object(installer.time, 'monotonic', side_effect=[0, 26]):
            with self.assertRaises(OSError):
                installer.start()
        self.start.assert_not_called()
        self.assertFalse((self.program/'install.json').exists())

    def test_successful_start_commits_receipt_after_daemon_confirmation(self):
        with patch.object(installer.cdp, 'probe_port', return_value={'Browser': 'test'}):
            installer.start()
        self.assertFalse(json.loads((self.program/'install.json').read_text())['pending'])
        self.start.assert_called_once()

    def test_internal_setup_entry_never_opens_terminal_or_public_cli(self):
        with patch.object(paths, 'frozen', return_value=True), \
                patch('wuwa_tappo_plus.console.open_console') as console, \
                patch.object(installer, 'main', return_value=0) as operation:
            self.assertEqual(entry.main(['--installer', 'prepare']), 0)
        operation.assert_called_once_with(['prepare'])
        console.assert_not_called()


if __name__ == '__main__':
    unittest.main()
