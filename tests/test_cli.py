"""Public CLI and configuration reload behavior, with all effects mocked."""

import io
import unittest
import tempfile
import tomllib
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from wuwa_tappo_plus import cli, menu, win
from wuwa_tappo_plus.config import Config, DEFAULT_TOML, save_preferences


class CommandTests(unittest.TestCase):
    def test_help_shows_only_public_commands(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["help"]), 0)
        lines = output.getvalue().splitlines()
        names = [line.split()[0] for line in lines if line.startswith("  ")]
        self.assertEqual(set(names), set(cli.PUBLIC_COMMANDS))

    def test_removed_commands_are_rejected(self):
        for name in ("start", "stop", "restart", "app", "config", "policy"):
            with self.subTest(name=name), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([name]), 1)
                self.assertNotIn(name, cli.COMMANDS)

    def test_internal_commands_remain_available(self):
        for name in ("menu", "run", "ensure"):
            self.assertIn(name, cli.COMMANDS)
            self.assertNotIn(name, cli.PUBLIC_COMMANDS)

    def test_doctor_delegates_policy_options(self):
        for args in (["--policy"], ["--clean-policy"],
                     ["--clean-policy", "--yes"]):
            with self.subTest(args=args), \
                    patch.object(cli, "_doctor_policy", return_value=0) as policy:
                self.assertEqual(cli.cmd_doctor(args), 0)
                policy.assert_called_once_with(args)

    def test_doctor_rejects_invalid_options(self):
        for args in (["--yes"], ["--clean"], ["--unknown"]):
            with self.subTest(args=args), redirect_stdout(io.StringIO()), \
                    patch.object(cli, "_doctor_policy") as policy:
                self.assertEqual(cli.cmd_doctor(args), 1)
                policy.assert_not_called()

    def policy_mocks(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for target, value in (
            ("wuwa_tappo_plus.cli.load", Config({})),
            ("wuwa_tappo_plus.cli.out", None),
            ("wuwa_tappo_plus.win.policy_report", []),
            ("wuwa_tappo_plus.win.stray_policy_values", [("HKLM", None, "other.exe", "args")]),
            ("wuwa_tappo_plus.win.is_admin", True),
            ("wuwa_tappo_plus.cli.sys.stdin.isatty", False),
        ):
            stack.enter_context(patch(target, return_value=value))
        return stack.enter_context(patch.object(win, "remove_stray_values",
                                               return_value=["HKLM\\other.exe"]))

    def test_policy_inspection_does_not_delete(self):
        remove = self.policy_mocks()
        self.assertEqual(cli.cmd_doctor(["--policy"]), 0)
        remove.assert_not_called()

    def test_noninteractive_cleanup_requires_yes(self):
        remove = self.policy_mocks()
        self.assertEqual(cli.cmd_doctor(["--clean-policy"]), 1)
        remove.assert_not_called()

    def test_explicit_cleanup_accepts_yes(self):
        remove = self.policy_mocks()
        self.assertEqual(cli.cmd_doctor(["--clean-policy", "--yes"]), 0)
        remove.assert_called_once_with(9222)

    def test_panel_uses_public_install(self):
        with patch.object(menu, "_collect", return_value={"daemon_alive": False}), \
                patch.object(menu, "_run") as run:
            menu._install()
        run.assert_called_once_with("安装 / 修复", cli.cmd_install)


class InstallTests(unittest.TestCase):
    def startup_mocks(self, running=True):
        stack = ExitStack()
        self.addCleanup(stack.close)
        values = {
            "load": Config({"inject": {"min_balance": 9000}}),
            "out": None,
            "daemon_status": ({}, None),
            "win.is_admin": True,
            "win.interpreter_label": "bundled",
            "win.find_exe": Path("WuwaTappo.exe"),
            "win.policy_conflicts": [],
            "_write_config_exe": None,
            "win.write_policy": "args",
            "win.processes": [(42, None)] if running else [],
            "win.restart_app": Path("WuwaTappo.exe"),
            "win.launch_app": None,
            "cdp.probe_port": {"Browser": "test"},
            "win.enable_autostart": "command",
            "win.register_watchdog": True,
            "_start_daemon": 0,
            "_stop_daemon": 0,
            "time.sleep": None,
        }
        return {
            name: stack.enter_context(patch("wuwa_tappo_plus.cli." + name, return_value=value))
            for name, value in values.items()
        }

    def test_install_rebuilds_page_before_starting_daemon(self):
        mocks = self.startup_mocks()
        events = []
        for name in ("_stop_daemon", "win.restart_app", "_start_daemon"):
            value = mocks[name].return_value
            mocks[name].side_effect = lambda *a, n=name, v=value: events.append(n) or v
        self.assertEqual(cli.cmd_install([]), 0)
        self.assertEqual(events, ["_stop_daemon", "win.restart_app", "_start_daemon"])
        mocks["win.enable_autostart"].assert_called_once()
        mocks["win.register_watchdog"].assert_called_once()

    def test_install_launches_closed_app(self):
        mocks = self.startup_mocks(running=False)
        self.assertEqual(cli.cmd_install([]), 0)
        mocks["win.launch_app"].assert_called_once()
        mocks["win.restart_app"].assert_not_called()

    def test_reinstall_repairs_even_when_daemon_is_running(self):
        mocks = self.startup_mocks()
        mocks["daemon_status"].return_value = ({}, 42)
        self.assertEqual(cli.cmd_install([]), 0)
        mocks["_stop_daemon"].assert_called_once()
        mocks["win.write_policy"].assert_called_once()
        mocks["win.restart_app"].assert_called_once()

    def test_conflicting_install_changes_nothing(self):
        mocks = self.startup_mocks()
        mocks["win.policy_conflicts"].return_value = [
            {"kind": "value", "hive": "HKLM", "name": win.TARGET_EXE, "value": "other"}
        ]
        self.assertEqual(cli.cmd_install([]), 1)
        mocks["_write_config_exe"].assert_not_called()
        mocks["win.write_policy"].assert_not_called()
        mocks["_start_daemon"].assert_not_called()

    def test_failed_page_restart_does_not_start_daemon(self):
        mocks = self.startup_mocks()
        mocks["win.restart_app"].side_effect = OSError("access denied")
        self.assertEqual(cli.cmd_install([]), 1)
        mocks["_start_daemon"].assert_not_called()

    def test_install_elevates_before_stopping(self):
        mocks = self.startup_mocks()
        mocks["win.is_admin"].return_value = False
        with patch.object(win, "relaunch_elevated", return_value=True) as elevate:
            self.assertEqual(cli.cmd_install([]), 0)
        elevate.assert_called_once_with(["install", "--elevated"])
        mocks["_stop_daemon"].assert_not_called()

    def test_failed_daemon_stop_aborts_install_without_policy_changes(self):
        mocks = self.startup_mocks()
        mocks["_stop_daemon"].return_value = 1
        self.assertEqual(cli.cmd_install([]), 1)
        mocks["_write_config_exe"].assert_not_called()
        mocks["win.write_policy"].assert_not_called()
        mocks["win.restart_app"].assert_not_called()

    def test_reinstall_preserves_disabled_feature_preferences(self):
        writer = cli._write_config_exe
        mocks = self.startup_mocks()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(DEFAULT_TOML, encoding="utf-8")
            saved = save_preferences({"auto_open": False, "bubble": False,
                                      "animation": False}, path)
            mocks["load"].return_value = saved
            mocks["_write_config_exe"].side_effect = writer
            with patch.object(cli, "CONFIG_PATH", path):
                self.assertEqual(cli.cmd_install([]), 0)
            after = Config(tomllib.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(after.preferences, saved.preferences)
        self.assertEqual(after.exe, "WuwaTappo.exe")

    def test_conflict_does_not_stop_an_existing_daemon(self):
        mocks = self.startup_mocks()
        mocks["daemon_status"].return_value = ({}, 42)
        mocks["win.policy_conflicts"].return_value = [
            {"kind": "port", "hive": "HKLM", "name": "other.exe", "value": "args"}
        ]
        self.assertEqual(cli.cmd_install([]), 1)
        mocks["_stop_daemon"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
