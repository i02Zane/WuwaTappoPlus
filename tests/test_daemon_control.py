"""Daemon lifecycle checks with isolated markers and simulated processes."""

import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from wuwa_tappo_plus import cli, daemon_control as control


class DaemonControlTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        for name, filename in (("DISABLED_PATH", ".disabled"), ("STOP_FILE", ".stop"),
                               ("STATE_PATH", ".state.json"), ("LOCK_PATH", ".daemon.lock")):
            stack.enter_context(patch.object(control, name, self.root / filename))
        self.sleep = stack.enter_context(patch.object(control.time, "sleep"))
        self.launch = stack.enter_context(patch.object(control.win, "launch_detached"))
        stack.enter_context(patch.object(control.win, "command_args", return_value=["test", "run"]))
        self.alive = stack.enter_context(patch.object(control.win, "pid_alive", return_value=False))
        self.kill = stack.enter_context(patch.object(control.win, "kill_pid", return_value=True))
        self.lock = stack.enter_context(patch.object(control, "lock_held", return_value=False))

    def test_status_rejects_malformed_state_and_invalid_pid(self):
        for value in ([], None, "bad", {"pid": True}, {"pid": "12"}, {"pid": -1}):
            with self.subTest(value=value):
                control.STATE_PATH.write_text(json.dumps(value), encoding="utf-8")
                self.assertIsNone(control.status()[1])
        self.alive.assert_not_called()

    def test_missing_and_corrupt_state_are_empty(self):
        self.assertEqual(control.read_state(), {})
        control.STATE_PATH.write_text("{", encoding="utf-8")
        self.assertEqual(control.read_state(), {})

    def test_unknown_pid_with_active_lock_aborts_without_killing(self):
        self.lock.return_value = True
        result = control.stop()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.steps[-1].step, "stop_daemon")
        self.kill.assert_not_called()
        self.assertTrue(control.DISABLED_PATH.exists())

    def test_no_daemon_is_a_successful_skip(self):
        self.assertEqual(control.stop().steps[-1].status, "skipped")
        self.kill.assert_not_called()

    def test_graceful_stop_waits_for_both_process_and_lock(self):
        with patch.object(control, "status", return_value=({}, 42)):
            self.lock.side_effect = [True, False]
            result = control.stop()
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.sleep.call_count, 2)
        self.kill.assert_not_called()
        self.assertFalse(control.STOP_FILE.exists())

    def test_successful_kill_is_not_enough_if_process_survives(self):
        self.alive.return_value = True
        with patch.object(control, "status", return_value=({}, 42)):
            result = control.stop()
        self.assertEqual(result.status, "failed")
        self.kill.assert_called_once_with(42)
        self.assertTrue(control.STOP_FILE.exists())

    def test_successful_kill_is_not_enough_if_lock_survives(self):
        self.alive.side_effect = [True] * 21 + [False]
        self.lock.return_value = True
        with patch.object(control, "status", return_value=({}, 42)):
            result = control.stop()
        self.assertEqual(result.status, "failed")
        self.kill.assert_called_once_with(42)

    def test_forced_stop_succeeds_only_after_rechecking_exit(self):
        self.alive.side_effect = [True] * 21 + [False]
        with patch.object(control, "status", return_value=({}, 42)):
            result = control.stop()
        self.assertEqual(result.status, "completed")
        self.kill.assert_called_once_with(42)
        self.assertFalse(control.STOP_FILE.exists())

    def test_dead_pid_and_held_lock_never_kills_an_unknown_owner(self):
        self.lock.return_value = True
        with patch.object(control, "status", return_value=({}, 42)):
            self.assertEqual(control.stop().status, "failed")
        self.kill.assert_not_called()

    def test_disarm_failure_aborts_before_requesting_stop(self):
        with patch.object(control, "_disarm", side_effect=PermissionError("denied")):
            self.assertEqual(control.stop().status, "failed")
        self.assertFalse(control.STOP_FILE.exists())
        self.kill.assert_not_called()

    def test_start_reuses_a_starting_instance_without_deleting_its_lock(self):
        self.lock.return_value = True
        control.LOCK_PATH.write_text("owned", encoding="utf-8")
        control.DISABLED_PATH.write_text("disabled", encoding="utf-8")
        with patch.object(control, "status", side_effect=[({}, None), ({}, 42)]):
            result = control.start()
        self.assertEqual(result.status, "completed")
        self.launch.assert_not_called()
        self.assertEqual(control.LOCK_PATH.read_text(), "owned")
        self.assertFalse(control.DISABLED_PATH.exists())

    def test_start_clears_old_stop_marker_and_reports_progress(self):
        control.STOP_FILE.write_text("old", encoding="utf-8")
        events = []
        with patch.object(control, "status", side_effect=[({}, None), ({}, 42)]):
            result = control.start(events.append)
        self.launch.assert_called_once_with(["test", "run"])
        self.assertFalse(control.STOP_FILE.exists())
        self.assertEqual(events, result.steps)

    def test_start_failure_is_a_structured_result(self):
        self.launch.side_effect = OSError("blocked")
        self.assertEqual(control.start().status, "failed")

    def test_start_timeout_does_not_claim_a_running_daemon(self):
        self.assertEqual(control.start().status, "failed")
        self.assertEqual(self.sleep.call_count, 30)
        self.launch.assert_called_once()

    def test_ensure_does_not_revive_a_disarmed_daemon(self):
        control.DISABLED_PATH.write_text("disabled", encoding="utf-8")
        self.assertEqual(control.ensure(), 0)
        self.launch.assert_not_called()

    def test_ensure_respects_a_lock_before_state_is_published(self):
        self.lock.return_value = True
        self.assertEqual(control.ensure(), 0)
        self.launch.assert_not_called()

    def test_ensure_recovers_silently_and_cli_delegates(self):
        control.STOP_FILE.write_text("old", encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.cmd_ensure([]), 0)
        self.assertEqual(output.getvalue(), "")
        self.launch.assert_called_once()
        self.assertFalse(control.STOP_FILE.exists())


if __name__ == "__main__":
    unittest.main()
