"""Lifecycle regressions without registry writes or real process operations."""

import subprocess
import os
import io
import tempfile
import tomllib
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from wuwa_tappo_plus import cli, daemon_control, lifecycle, menu, win
from wuwa_tappo_plus.operations import OperationResult
from wuwa_tappo_plus.config import Config


class LifecycleTests(unittest.TestCase):
    def cleanup_mocks(self, app_running=True):
        stack = ExitStack()
        self.addCleanup(stack.close)
        targets = {
            "read_config_hints": Config({}),
            "installation.remove_program": "removed",
            "daemon_control.status": ({}, None),
            "daemon_control.lock_held": False,
            "daemon_control.stop": OperationResult(),
            "win.is_admin": True,
            "win.processes": [(42, None)] if app_running else [],
            "win.remove_policy": True,
            "win.stray_policy_values": [],
            "win.disable_autostart": True,
            "win.remove_watchdog": True,
            "win.read_policy": {},
            "win.read_autostart": None,
            "win.read_watchdog": None,
            "win.restart_app": "WuwaTappo.exe",
        }
        return {
            name: stack.enter_context(patch("wuwa_tappo_plus.lifecycle." + name, return_value=value))
            for name, value in targets.items()
        }

    def test_uninstall_cleans_setup_then_restarts_app(self):
        mocks = self.cleanup_mocks()
        events = []
        for name in ("daemon_control.stop", "win.remove_policy", "win.disable_autostart",
                     "win.remove_watchdog", "win.restart_app"):
            value = mocks[name].return_value
            mocks[name].side_effect = lambda *a, n=name, v=value: events.append(n) or v
        self.assertEqual(cli.cmd_uninstall([]), 0)
        self.assertEqual(events, ["daemon_control.stop", "win.remove_policy",
                                  "win.disable_autostart", "win.remove_watchdog",
                                  "win.restart_app"])

    def test_uninstall_does_not_launch_an_app_that_was_closed(self):
        mocks = self.cleanup_mocks(app_running=False)
        self.assertEqual(cli.cmd_uninstall([]), 0)
        mocks["win.restart_app"].assert_not_called()

    def test_failed_daemon_stop_aborts_cleanup(self):
        mocks = self.cleanup_mocks()
        mocks["daemon_control.stop"].return_value = OperationResult("failed")
        self.assertEqual(cli.cmd_uninstall([]), 1)
        mocks["win.remove_policy"].assert_not_called()
        mocks["win.restart_app"].assert_not_called()

    def test_remaining_policy_is_reported_as_failure(self):
        mocks = self.cleanup_mocks()
        mocks["win.read_policy"].return_value = {win.TARGET_EXE: "debug args"}
        self.assertEqual(cli.cmd_uninstall([]), 1)
        mocks["win.restart_app"].assert_not_called()

    def test_app_restart_failure_is_reported(self):
        mocks = self.cleanup_mocks()
        mocks["win.restart_app"].side_effect = OSError("access denied")
        self.assertEqual(cli.cmd_uninstall([]), 1)

    def test_uninstall_requests_elevation_before_cleanup(self):
        mocks = self.cleanup_mocks()
        mocks["win.is_admin"].return_value = False
        with patch.object(win, "relaunch_elevated", return_value=True) as elevate:
            self.assertEqual(cli.cmd_uninstall([]), 0)
        elevate.assert_called_once_with(["uninstall", "--elevated"])
        mocks["daemon_control.stop"].assert_not_called()

    def test_panel_uses_cli_uninstall_even_when_nothing_is_running(self):
        with patch.object(menu, "_run") as run:
            menu._uninstall()
        run.assert_called_once_with("卸载（保留数据）", cli.cmd_uninstall)

    def test_regular_uninstall_never_clears_data(self):
        self.cleanup_mocks()
        with patch.object(lifecycle, "purge_data") as purge:
            self.assertEqual(cli.cmd_uninstall([]), 0)
        purge.assert_not_called()

    def test_purge_runs_only_after_successful_uninstall(self):
        mocks = self.cleanup_mocks()
        events = []
        mocks["win.restart_app"].side_effect = lambda *a: events.append("restart") or "app"
        with patch.object(lifecycle, "purge_data", side_effect=lambda: events.append("purge")):
            self.assertEqual(cli.cmd_uninstall(["--purge", "--yes"]), 0)
        self.assertEqual(events, ["restart", "purge"])

    def test_failed_uninstall_preserves_data(self):
        for failure in ("daemon_control.stop", "win.read_watchdog", "win.restart_app"):
            with self.subTest(failure=failure):
                mocks = self.cleanup_mocks()
                mocks[failure].return_value = (OperationResult("failed") if failure == "daemon_control.stop"
                                               else 1 if failure == "win.read_watchdog" else None)
                with patch.object(lifecycle, "purge_data") as purge:
                    self.assertEqual(cli.cmd_uninstall(["--purge", "--yes"]), 1)
                purge.assert_not_called()

    def test_purge_confirmation_precedes_any_changes(self):
        mocks = self.cleanup_mocks()
        with patch.object(cli.sys, "stdin", new=io.StringIO()), \
                patch.object(lifecycle, "purge_data") as purge:
            self.assertEqual(cli.cmd_uninstall(["--purge"]), 1)
        mocks["read_config_hints"].assert_not_called()
        mocks["daemon_control.stop"].assert_not_called()
        purge.assert_not_called()

    def test_cancelled_purge_does_not_uninstall(self):
        mocks = self.cleanup_mocks()
        with patch.object(cli.sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", return_value="no"):
            self.assertEqual(cli.cmd_uninstall(["--purge"]), 1)
        mocks["daemon_control.stop"].assert_not_called()

    def test_confirmed_purge_is_forwarded_through_elevation(self):
        mocks = self.cleanup_mocks()
        mocks["win.is_admin"].return_value = False
        with patch.object(cli.sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", return_value="yes"), \
                patch.object(win, "relaunch_elevated", return_value=True) as elevate:
            self.assertEqual(cli.cmd_uninstall(["--purge"]), 0)
        elevate.assert_called_once_with(["uninstall", "--elevated", "--purge", "--yes"])

    def test_data_cleanup_error_is_not_reported_as_success(self):
        self.cleanup_mocks()
        with patch.object(lifecycle, "purge_data", side_effect=PermissionError("locked")):
            self.assertEqual(cli.cmd_uninstall(["--purge", "--yes"]), 1)

    def test_both_uninstall_modes_remove_installed_program_files(self):
        mocks = self.cleanup_mocks()
        for args in ([], ["--purge", "--yes"]):
            with patch.object(lifecycle, "purge_data"):
                self.assertEqual(cli.cmd_uninstall(args), 0)
        self.assertEqual(mocks["installation.remove_program"].call_count, 2)

    def test_regular_uninstall_menu_exits_after_success(self):
        with patch.object(menu, "_collect", return_value={}) as collect, \
                patch.object(menu, "_frame", return_value=[]), \
                patch("builtins.input", return_value="2"), \
                patch.dict(menu.ACTIONS, {"2": ("uninstall", lambda: 0)}):
            self.assertEqual(menu._plain_menu(), 0)
        collect.assert_called_once()

    def test_purge_menu_exits_without_recollecting_and_recreating_config(self):
        with patch.object(menu, "_collect", return_value={}) as collect, \
                patch.object(menu, "_frame", return_value=[]), \
                patch("builtins.input", return_value="3"), \
                patch.dict(menu.ACTIONS, {"3": ("purge", lambda: 0)}):
            self.assertEqual(menu._plain_menu(), 0)
        collect.assert_called_once()

    def test_menu_once_handles_windowless_entry_without_stdin(self):
        with patch.object(menu.sys, "stdin", None), \
                patch.object(menu.sys, "stdout", new=io.StringIO()), \
                patch.object(menu, "_collect", return_value={}), \
                patch.object(menu, "_frame", return_value=["menu"]):
            self.assertEqual(menu.main(["--once"]), 0)

    def test_stop_app_retries_a_transient_kill_failure(self):
        results = [False, True]
        with patch.object(win, "find_exe", return_value="WuwaTappo.exe"), \
                patch.object(win, "processes", side_effect=[[(42, "WuwaTappo.exe")]] + [[]] * 10), \
                patch.object(win, "kill_pid", side_effect=lambda _pid, **kwargs: results.pop(0)), \
                patch.object(win, "pid_alive", return_value=True), \
                patch.object(win.time, "sleep"):
            self.assertEqual(win.stop_app(), "WuwaTappo.exe")
        self.assertEqual(results, [])

    def test_stop_app_reports_when_the_app_survives_every_attempt(self):
        with patch.object(win, "find_exe", return_value="WuwaTappo.exe"), \
                patch.object(win, "processes", side_effect=[[(42, "WuwaTappo.exe")]] + [[]] * 10), \
                patch.object(win, "kill_pid", return_value=False) as kill, \
                patch.object(win, "pid_alive", return_value=True), \
                patch.object(win.time, "sleep"):
            with self.assertRaises(OSError):
                win.stop_app()
        self.assertEqual(kill.call_count, 3)

    def test_stop_app_ignores_a_pid_that_vanished_before_the_kill(self):
        with patch.object(win, "find_exe", return_value="WuwaTappo.exe"), \
                patch.object(win, "processes", side_effect=[[(42, "WuwaTappo.exe")]] + [[]] * 10), \
                patch.object(win, "kill_pid", return_value=False), \
                patch.object(win, "pid_alive", return_value=False), \
                patch.object(win.time, "sleep"):
            self.assertEqual(win.stop_app(), "WuwaTappo.exe")

    def test_app_restart_does_not_launch_after_failed_kill(self):
        with patch.object(win, "find_exe", return_value="WuwaTappo.exe"), \
                patch.object(win, "processes", return_value=[(42, "WuwaTappo.exe")]), \
                patch.object(win, "kill_pid", return_value=False), \
                patch.object(win, "launch_app") as launch:
            with self.assertRaises(OSError):
                win.restart_app()
        launch.assert_not_called()


class ReadonlyUninstallTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.root / "config.toml"
        stack.enter_context(patch.object(lifecycle, "CONFIG_PATH", self.path))
        stack.enter_context(patch.object(lifecycle, "DATA_DIR", self.root))
        stack.enter_context(patch.object(cli, "out"))
        self.load = stack.enter_context(patch.object(cli, "load", side_effect=AssertionError("must not load")))
        values = {
            "daemon_control.stop": OperationResult(),
            "daemon_control.status": ({}, None),
            "daemon_control.lock_held": False,
            "win.is_admin": True,
            "win.processes": [],
            "win.remove_policy": True,
            "win.stray_policy_values": [],
            "win.disable_autostart": True,
            "win.remove_watchdog": True,
            "win.read_policy": {},
            "win.read_autostart": None,
            "win.read_watchdog": None,
            "win.restart_app": "app",
            "installation.remove_program": "removed",
        }
        self.mocks = {name: stack.enter_context(patch("wuwa_tappo_plus.lifecycle." + name,
                                                    return_value=value))
                      for name, value in values.items()}

    def test_missing_config_does_not_prevent_uninstall_or_create_files(self):
        result = cli.cmd_uninstall([]).operation
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.steps[0].status, "warning")
        self.assertEqual(list(self.root.iterdir()), [])
        self.load.assert_not_called()

    def test_menu_remains_accessible_with_missing_or_broken_config(self):
        for content in (None, "[broken"):
            with self.subTest(content=content):
                if content is not None:
                    self.path.write_text(content, encoding="utf-8")
                panel = menu._collect()
                self.assertEqual(panel["port"], 9222)
                self.assertTrue(menu._frame(panel))
                if content is None:
                    self.assertFalse(self.path.exists())
                else:
                    self.assertEqual(self.path.read_text(), content)
        self.load.assert_not_called()

    def test_broken_config_is_preserved_in_regular_uninstall(self):
        for content in (b"[broken", b"\xff", b"app = 1", b"[app]\nport = 'bad'",
                        b"[app]\nexe = false", b"[app]\nport = true"):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                result = cli.cmd_uninstall([]).operation
                self.assertEqual(result.status, "completed")
                self.assertEqual(result.steps[0].status, "warning")
                self.assertEqual(self.path.read_bytes(), content)
        self.load.assert_not_called()

    def test_purge_can_remove_broken_config_after_cleanup(self):
        self.path.write_text("[broken", encoding="utf-8")
        self.assertEqual(cli.cmd_uninstall(["--purge", "--yes"]), 0)
        self.assertFalse(self.root.exists())
        self.load.assert_not_called()

    def test_valid_config_is_not_reconciled_and_supplies_restart_hint(self):
        content = b'\xef\xbb\xbf[app]\nexe = "D:/pet/WuwaTappo.exe"\nport = 9333\n[retired]\nx = 1\n'
        self.path.write_bytes(content)
        self.mocks["win.processes"].return_value = [(42, None)]
        self.assertEqual(cli.cmd_uninstall([]), 0)
        self.mocks["win.restart_app"].assert_called_once_with("D:/pet/WuwaTappo.exe")
        self.mocks["win.stray_policy_values"].assert_called_once_with(9333)
        self.assertEqual(self.path.read_bytes(), content)

    def test_unlocatable_running_pet_preserves_data_and_program(self):
        self.path.write_text("[broken", encoding="utf-8")
        self.mocks["win.processes"].return_value = [(42, None)]
        self.mocks["win.restart_app"].return_value = None
        result = cli.cmd_uninstall(["--purge", "--yes"]).operation
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.steps[-1].step, "restart_app")
        self.assertTrue(self.path.exists())
        self.mocks["installation.remove_program"].assert_not_called()

    def test_lock_reappearing_after_cleanup_prevents_program_and_data_deletion(self):
        self.path.write_text("keep", encoding="utf-8")
        self.mocks["daemon_control.lock_held"].return_value = True
        result = cli.cmd_uninstall(["--purge", "--yes"]).operation
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.steps[-1].step, "verify_daemon_exit")
        self.assertTrue(self.path.exists())
        self.mocks["installation.remove_program"].assert_not_called()

    def test_deferred_removal_reports_only_future_deletion(self):
        self.mocks["installation.remove_program"].return_value = "deferred"
        result = lifecycle.uninstall()
        self.assertEqual(result.status, "completed")
        self.assertIn("退出窗口后", result.steps[-1].message)
        self.assertNotIn("已删除", result.steps[-1].message)


class AppConfigWriteTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.root / "config.toml"
        stack.enter_context(patch.object(lifecycle, "CONFIG_PATH", self.path))

    def test_only_app_section_is_updated_and_bom_is_removed(self):
        self.path.write_text("[app]\nport = 9222\n# keep\n[other]\nport = 1234\n", encoding="utf-8-sig")
        lifecycle.write_config_port(9333)
        data = tomllib.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["app"]["port"], 9333)
        self.assertEqual(data["other"]["port"], 1234)
        self.assertIn("# keep", self.path.read_text())
        self.assertFalse(self.path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_windows_path_with_quotes_roundtrips(self):
        self.path.write_text("[app]\nexe = ''\n", encoding="utf-8")
        value = "D:\\User's pets\\WuwaTappo.exe"
        lifecycle.write_config_exe(value)
        self.assertEqual(tomllib.loads(self.path.read_text())["app"]["exe"], value)

    def test_failed_atomic_replace_preserves_config_and_removes_temp(self):
        self.path.write_text("[app]\nport = 9222\n", encoding="utf-8")
        original = self.path.read_bytes()
        with patch.object(lifecycle.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaises(OSError):
                lifecycle.write_config_port(9333)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [self.path])


class PurgeDataTests(unittest.TestCase):
    def test_known_data_removed_but_source_exe_and_other_files_survive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            removed = ["accessory_overrides.json", ".plus-accessories-sample.tmp",
                       "chests.json", "chests.tmp", "plaza_auth.json", "plaza_auth.tmp", "config.toml", ".state.json", ".state.tmp", ".daemon.lock",
                       ".stop", ".disabled", ".plus-config-sample.tmp", "logs/auto.log",
                       "logs/auto.log.1", "reports/input.sqlite3", "reports/input.sqlite3-wal",
                       "reports/rooms.sqlite3", "reports/rooms.sqlite3-shm"]
            kept = ["launch.py", "WuwaTappoPlus.exe", "src/code.py",
                    "logs/notes.txt", "reports/unrelated.sqlite3", "unrelated.tmp"]
            for name in removed + kept:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("test", encoding="utf-8")
            with patch.object(lifecycle, "DATA_DIR", root), \
                    patch.object(daemon_control, "status", return_value=({}, None)), \
                    patch.object(daemon_control, "lock_held", return_value=False):
                lifecycle.purge_data()
            self.assertTrue(all(not (root / name).exists() for name in removed))
            self.assertTrue(all((root / name).is_file() for name in kept))

    def test_empty_app_data_directory_is_removed_and_repeated_cleanup_is_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "data"
            root.mkdir()
            (root / "config.toml").write_text("test", encoding="utf-8")
            (root / "accessory_overrides.json").write_text("{}", encoding="utf-8")
            with patch.object(lifecycle, "DATA_DIR", root), \
                    patch.object(daemon_control, "status", return_value=({}, None)), \
                    patch.object(daemon_control, "lock_held", return_value=False):
                lifecycle.purge_data()
                lifecycle.purge_data()
            self.assertFalse(root.exists())

    def test_active_lock_prevents_any_data_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config.toml"
            config.write_text("keep", encoding="utf-8")
            with patch.object(lifecycle, "DATA_DIR", root), \
                    patch.object(daemon_control, "status", return_value=({}, None)), \
                    patch.object(daemon_control, "lock_held", return_value=True):
                with self.assertRaises(OSError):
                    lifecycle.purge_data()
            self.assertEqual(config.read_text(), "keep")

    def test_invalid_target_aborts_before_deleting_any_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config.toml").write_text("keep", encoding="utf-8")
            (root / ".state.json").mkdir()
            with patch.object(lifecycle, "DATA_DIR", root), \
                    patch.object(daemon_control, "status", return_value=({}, None)), \
                    patch.object(daemon_control, "lock_held", return_value=False):
                with self.assertRaises(OSError):
                    lifecycle.purge_data()
            self.assertTrue((root / "config.toml").exists())

    def test_linked_data_directory_is_rejected_before_any_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            # Match the canonical paths used by _purge_data, including CI temp aliases.
            root = Path(folder).resolve()
            (root / "config.toml").write_text("keep", encoding="utf-8")
            with patch.object(lifecycle, "DATA_DIR", root), \
                    patch.object(daemon_control, "status", return_value=({}, None)), \
                    patch.object(daemon_control, "lock_held", return_value=False), \
                    patch.object(Path, "is_symlink", autospec=True,
                                 side_effect=lambda path: path == root / "reports"):
                with self.assertRaises(OSError):
                    lifecycle.purge_data()
            self.assertTrue((root / "config.toml").exists())


    def test_linked_accessory_data_is_rejected_before_any_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            config = root / "config.toml"
            config.write_text("keep", encoding="utf-8")
            with patch.object(lifecycle, "DATA_DIR", root), \
                    patch.object(daemon_control, "status", return_value=({}, None)), \
                    patch.object(daemon_control, "lock_held", return_value=False), \
                    patch.object(Path, "is_symlink", autospec=True,
                                 side_effect=lambda path: path == root / "accessory_overrides.json"):
                with self.assertRaises(OSError):
                    lifecycle.purge_data()
            self.assertEqual(config.read_text(encoding="utf-8"), "keep")

    def test_real_junction_cannot_delete_external_data(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder).resolve()
            root = base / "data"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            config = root / "config.toml"
            config.write_text("keep", encoding="utf-8")
            database = outside / "input.sqlite3"
            database.write_text("external data", encoding="utf-8")
            link = root / "reports"
            try:
                try:
                    link.symlink_to(outside, target_is_directory=True)
                except OSError:
                    self.skipTest("directory link permission unavailable")
                with patch.object(lifecycle, "DATA_DIR", root), \
                        patch.object(daemon_control, "status", return_value=({}, None)), \
                        patch.object(daemon_control, "lock_held", return_value=False):
                    with self.assertRaises(OSError):
                        lifecycle.purge_data()
                self.assertEqual(config.read_text(encoding="utf-8"), "keep")
                self.assertEqual(database.read_text(encoding="utf-8"), "external data")
            finally:
                if link.exists():
                    link.rmdir()  # Remove only the junction, never its target.

if __name__ == "__main__":
    unittest.main()
