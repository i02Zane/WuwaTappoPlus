"""Lifecycle regressions without registry writes or real process operations."""

import subprocess
import unittest
from contextlib import ExitStack
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
        run.assert_called_once_with("卸载", cli.cmd_uninstall)

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


if __name__ == "__main__":
    unittest.main()
