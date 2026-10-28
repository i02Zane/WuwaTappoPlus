"""Lifecycle regressions without registry writes or real process operations."""

import subprocess
import os
import io
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from wuwa_tappo_plus import cli, menu, win
from wuwa_tappo_plus.config import Config


class LifecycleTests(unittest.TestCase):
    def cleanup_mocks(self, app_running=True):
        stack = ExitStack()
        self.addCleanup(stack.close)
        targets = {
            "load": Config({}),
            "out": None,
            "_stop_daemon": 0,
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
            name: stack.enter_context(patch("wuwa_tappo_plus.cli." + name, return_value=value))
            for name, value in targets.items()
        }

    def test_uninstall_cleans_setup_then_restarts_app(self):
        mocks = self.cleanup_mocks()
        events = []
        for name in ("_stop_daemon", "win.remove_policy", "win.disable_autostart",
                     "win.remove_watchdog", "win.restart_app"):
            value = mocks[name].return_value
            mocks[name].side_effect = lambda *a, n=name, v=value: events.append(n) or v
        self.assertEqual(cli.cmd_uninstall([]), 0)
        self.assertEqual(events, ["_stop_daemon", "win.remove_policy",
                                  "win.disable_autostart", "win.remove_watchdog",
                                  "win.restart_app"])

    def test_uninstall_does_not_launch_an_app_that_was_closed(self):
        mocks = self.cleanup_mocks(app_running=False)
        self.assertEqual(cli.cmd_uninstall([]), 0)
        mocks["win.restart_app"].assert_not_called()

    def test_failed_daemon_stop_aborts_cleanup(self):
        mocks = self.cleanup_mocks()
        mocks["_stop_daemon"].return_value = 1
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
        mocks["_stop_daemon"].assert_not_called()

    def test_panel_uses_cli_uninstall_even_when_nothing_is_running(self):
        with patch.object(menu, "_run") as run:
            menu._uninstall()
        run.assert_called_once_with("卸载（保留数据）", cli.cmd_uninstall)

    def test_regular_uninstall_never_clears_data(self):
        self.cleanup_mocks()
        with patch.object(cli, "_purge_data") as purge:
            self.assertEqual(cli.cmd_uninstall([]), 0)
        purge.assert_not_called()

    def test_purge_runs_only_after_successful_uninstall(self):
        mocks = self.cleanup_mocks()
        events = []
        mocks["win.restart_app"].side_effect = lambda *a: events.append("restart") or "app"
        with patch.object(cli, "_purge_data", side_effect=lambda: events.append("purge")):
            self.assertEqual(cli.cmd_uninstall(["--purge", "--yes"]), 0)
        self.assertEqual(events, ["restart", "purge"])

    def test_failed_uninstall_preserves_data(self):
        for failure in ("_stop_daemon", "win.read_watchdog", "win.restart_app"):
            with self.subTest(failure=failure):
                mocks = self.cleanup_mocks()
                mocks[failure].return_value = 1 if failure != "win.restart_app" else None
                with patch.object(cli, "_purge_data") as purge:
                    self.assertEqual(cli.cmd_uninstall(["--purge", "--yes"]), 1)
                purge.assert_not_called()

    def test_purge_confirmation_precedes_any_changes(self):
        mocks = self.cleanup_mocks()
        with patch.object(cli.sys, "stdin", new=io.StringIO()), \
                patch.object(cli, "_purge_data") as purge:
            self.assertEqual(cli.cmd_uninstall(["--purge"]), 1)
        mocks["load"].assert_not_called()
        mocks["_stop_daemon"].assert_not_called()
        purge.assert_not_called()

    def test_cancelled_purge_does_not_uninstall(self):
        mocks = self.cleanup_mocks()
        with patch.object(cli.sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", return_value="no"):
            self.assertEqual(cli.cmd_uninstall(["--purge"]), 1)
        mocks["_stop_daemon"].assert_not_called()

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
        with patch.object(cli, "_purge_data", side_effect=PermissionError("locked")):
            self.assertEqual(cli.cmd_uninstall(["--purge", "--yes"]), 1)

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

    def test_kill_checks_exit_code_and_process_exit(self):
        for returncode, alive, expected in ((1, True, False), (0, True, False),
                                             (0, False, True)):
            with self.subTest(returncode=returncode, alive=alive), \
                    patch.object(win.subprocess, "run", return_value=
                                 subprocess.CompletedProcess([], returncode)), \
                    patch.object(win, "pid_alive", return_value=alive):
                self.assertEqual(win.kill_pid(42), expected)

    def test_app_restart_does_not_launch_after_failed_kill(self):
        with patch.object(win, "find_exe", return_value="WuwaTappo.exe"), \
                patch.object(win, "running_pids", return_value=[42]), \
                patch.object(win, "kill_pid", return_value=False), \
                patch.object(win, "launch_app") as launch:
            with self.assertRaises(OSError):
                win.restart_app()
        launch.assert_not_called()


class PurgeDataTests(unittest.TestCase):
    def test_known_data_removed_but_source_exe_and_other_files_survive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            removed = ["chests.json", "chests.tmp", "config.toml", ".state.json", ".state.tmp", ".daemon.lock",
                       ".stop", ".disabled", ".plus-config-sample.tmp", "logs/auto.log",
                       "logs/auto.log.1", "reports/input.sqlite3", "reports/input.sqlite3-wal",
                       "reports/rooms.sqlite3", "reports/rooms.sqlite3-shm"]
            kept = ["launch.py", "WuwaTappoPlus.exe", "src/code.py",
                    "logs/notes.txt", "reports/unrelated.sqlite3"]
            for name in removed + kept:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("test", encoding="utf-8")
            with patch.object(cli, "DATA_DIR", root), \
                    patch.object(cli, "daemon_status", return_value=({}, None)), \
                    patch.object(cli, "_lock_held", return_value=False):
                cli._purge_data()
            self.assertTrue(all(not (root / name).exists() for name in removed))
            self.assertTrue(all((root / name).is_file() for name in kept))

    def test_empty_app_data_directory_is_removed_and_repeated_cleanup_is_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "data"
            root.mkdir()
            (root / "config.toml").write_text("test", encoding="utf-8")
            with patch.object(cli, "DATA_DIR", root), \
                    patch.object(cli, "daemon_status", return_value=({}, None)), \
                    patch.object(cli, "_lock_held", return_value=False):
                cli._purge_data()
                cli._purge_data()
            self.assertFalse(root.exists())

    def test_active_lock_prevents_any_data_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config.toml"
            config.write_text("keep", encoding="utf-8")
            with patch.object(cli, "DATA_DIR", root), \
                    patch.object(cli, "daemon_status", return_value=({}, None)), \
                    patch.object(cli, "_lock_held", return_value=True):
                with self.assertRaises(OSError):
                    cli._purge_data()
            self.assertEqual(config.read_text(), "keep")

    def test_invalid_target_aborts_before_deleting_any_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config.toml").write_text("keep", encoding="utf-8")
            (root / ".state.json").mkdir()
            with patch.object(cli, "DATA_DIR", root), \
                    patch.object(cli, "daemon_status", return_value=({}, None)), \
                    patch.object(cli, "_lock_held", return_value=False):
                with self.assertRaises(OSError):
                    cli._purge_data()
            self.assertTrue((root / "config.toml").exists())

    def test_linked_data_directory_is_rejected_before_any_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            # Match the canonical paths used by _purge_data, including CI temp aliases.
            root = Path(folder).resolve()
            (root / "config.toml").write_text("keep", encoding="utf-8")
            with patch.object(cli, "DATA_DIR", root), \
                    patch.object(cli, "daemon_status", return_value=({}, None)), \
                    patch.object(cli, "_lock_held", return_value=False), \
                    patch.object(Path, "is_symlink", autospec=True,
                                 side_effect=lambda path: path == root / "reports"):
                with self.assertRaises(OSError):
                    cli._purge_data()
            self.assertTrue((root / "config.toml").exists())


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
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                     "New-Item -ItemType Junction -Path $env:WTP_TEST_LINK "
                     "-Target $env:WTP_TEST_TARGET -ErrorAction Stop | Out-Null"],
                    env={**os.environ, "WTP_TEST_LINK": str(link),
                         "WTP_TEST_TARGET": str(outside)},
                    capture_output=True, timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                with patch.object(cli, "DATA_DIR", root), \
                        patch.object(cli, "daemon_status", return_value=({}, None)), \
                        patch.object(cli, "_lock_held", return_value=False):
                    with self.assertRaises(OSError):
                        cli._purge_data()
                self.assertEqual(config.read_text(encoding="utf-8"), "keep")
                self.assertEqual(database.read_text(encoding="utf-8"), "external data")
            finally:
                if link.exists():
                    link.rmdir()  # Remove only the junction, never its target.

if __name__ == "__main__":
    unittest.main()
