"""Page log transitions and rolling-buffer deduplication."""

import unittest
from datetime import datetime
from unittest.mock import Mock

from wuwa_tappo_plus.config import Config
from wuwa_tappo_plus.observations import PageObservations


class PageErrorTests(unittest.TestCase):
    def test_rolling_error_buffer_ignores_garbage_and_logs_each_note_once(self):
        observations = PageObservations(Mock())
        notes = [{"t": 1, "m": "no state change"}, {"t": 2, "m": "disconnected"}]
        for batch in ([None, "x", 3], [notes[0]], notes, notes):
            observations.drain_page_errors(batch)
        self.assertEqual([call.args[0] for call in observations.log.warn.call_args_list],
                         ["page: no state change", "page: disconnected"])


class BlockedClickTests(unittest.TestCase):
    """Follow visible blocking, reminders and recovery rather than private fields."""

    def setUp(self):
        self.now = 1700000000.0
        self.observations = PageObservations(
            Mock(), started=datetime.fromtimestamp(self.now), wall_clock=lambda: self.now)
        self.snapshot = {"now": 1000000, "left": 990000, "right": 1600000, "bal": 740}

    def test_block_changes_and_recovery_are_logged_once_with_actionable_details(self):
        observations = self.observations
        for reason in ("no-button", "no-button", "button-disabled", None, None):
            block = {"reason": reason, "balance": 740} if reason else None
            observations.note_block(block, self.snapshot)
        messages = [call.args[0] for call in observations.log.warn.call_args_list]
        self.assertEqual(len(messages), 2)
        self.assertIn("no-button", messages[0])
        self.assertIn("balance=740", messages[0])
        self.assertIn("chest 10s overdue", messages[0])
        self.assertIn("button-disabled", messages[1])
        observations.log.info.assert_called_once()
        self.assertIn("open again", observations.log.info.call_args.args[0])
        self.assertIsNone(observations.block_reason)

    def test_block_identity_distinguishes_slots_and_chests_but_deduplicates_each(self):
        observations = self.observations
        # Same reason, a second slot at the same timestamp, then a new chest.
        for slot, stamp in (("left", 1000000), ("right", 1000000), ("right", 1005000)):
            block = {"reason": "low-balance", "slot": slot, "ts": stamp, "balance": 508}
            for _ in range(2):
                observations.note_block(block, self.snapshot)
        self.assertEqual(observations.log.warn.call_count, 3)
        self.assertEqual(observations.block_reason, "low-balance")

    def test_persistent_blocks_remind_at_ten_minutes_with_the_right_severity(self):
        for reason, level in (("paused", "info"), ("no-button", "warn")):
            with self.subTest(reason=reason):
                self.now = 1000
                observations = PageObservations(Mock(), wall_clock=lambda: self.now)
                block = {"reason": reason, "slot": "left", "ts": 990000, "balance": 740}
                observations.note_block(block, self.snapshot)
                self.now += 599
                observations.note_block(block, self.snapshot)
                writer = getattr(observations.log, level)
                writer.assert_called_once()
                self.now += 1
                observations.note_block(block, self.snapshot)
                self.assertEqual(writer.call_count, 2)
                self.assertIn("still waiting", writer.call_args.args[0])
                other = observations.log.warn if level == "info" else observations.log.info
                other.assert_not_called()

    def test_give_up_reports_the_slot_and_attempt_count(self):
        self.observations.note_block({"reason": "give-up", "slot": "left", "ts": 990000,
                                      "balance": 1508, "attempts": 5}, self.snapshot)
        message = self.observations.log.warn.call_args.args[0]
        self.assertIn("give-up", message)
        self.assertIn("slot=left", message)
        self.assertIn("after 5 attempts", message)

    def test_click_settling_suppresses_transient_blocks_then_reports_a_real_stall(self):
        observations = self.observations
        observations.drain_clicks([{"t": int(self.now * 1000), "x": 1, "y": 2}])
        self.now += 1
        block = {"reason": "button-disabled", "balance": 2385}
        observations.note_block(block, self.snapshot)
        observations.log.warn.assert_not_called()
        self.assertIsNone(observations.block_reason)
        self.now += 5
        observations.note_block(block, self.snapshot)
        observations.log.warn.assert_called_once()


class HiddenChestBalanceTests(unittest.TestCase):
    def test_disabled_reminders_reset_baseline_without_false_recovery(self):
        runner = PageObservations(Mock())
        runner.alert_level = 2
        cfg = Config({"alerts": {"balance": False}})
        runner.alert_balance(cfg, {"bal": 500}, 0)
        self.assertIsNone(runner.alert_level)
        runner.log.info.assert_not_called()
        runner.log.warn.assert_not_called()
        cfg = Config({})
        runner.alert_balance(cfg, {"bal": 500}, 2)
        runner.log.warn.assert_not_called()
        runner.alert_balance(cfg, {"bal": 3000}, 0)
        runner.log.info.assert_called_once()

    def test_hidden_chests_do_not_suppress_balance_changes(self):
        runner = PageObservations(Mock())
        cfg = Config({})
        runner.alert_level = None
        runner.alert_balance(cfg, {"left": 0, "right": 0, "bal": 3000}, 0)
        runner.alert_balance(cfg, {"left": 0, "right": 0, "bal": 0}, 2)
        self.assertEqual(runner.alert_level, 2)
        runner.log.warn.assert_called_once()

    def test_unreadable_wallet_does_not_change_baseline(self):
        runner = PageObservations(Mock())
        cfg = Config({})
        runner.alert_level = 0
        runner.alert_balance(cfg, {"left": 100000, "bal": None}, 2)
        self.assertEqual(runner.alert_level, 0)
        runner.log.warn.assert_not_called()


class ClickAndToastTests(unittest.TestCase):
    def test_repeated_page_buffer_does_not_recount_history_or_duplicate_current_clicks(self):
        observations = PageObservations(Mock(), started=datetime.fromtimestamp(1700000000))
        old = {"t": 1699999997000, "x": 1, "y": 1}
        current = {"t": 1699999999000, "x": 2, "y": 2, "ok": True}
        failed = {"t": 1700000000000, "x": 3, "y": 3, "ok": False, "err": "gone"}
        for _ in range(3):
            observations.drain_clicks([old, current, failed])
        self.assertEqual(observations.click_count, 2)
        self.assertEqual(observations.last_click_ms, 1700000000000)
        observations.log.info.assert_called_once()
        observations.log.error.assert_called_once()

    def test_toast_correlation_keeps_ten_second_window_and_log_levels(self):
        observations = PageObservations(Mock())
        observations.last_click_ms = 10000
        observations.log_toast(9999, "before")
        observations.log_toast(20000, "after")
        observations.log_toast(20001, "unrelated")
        observations.log.warn.assert_called_once()
        self.assertIn("10.0s after our click", observations.log.warn.call_args.args[0])
        self.assertEqual(observations.log.info.call_count, 2)

    def test_heartbeat_keeps_interval_and_unknown_countdown_without_repeated_logs(self):
        wall = Mock(return_value=1000)
        observations = PageObservations(Mock(), wall_clock=wall)
        cfg = Config({"daemon": {"heartbeat": 300}})
        snapshot = {"now": 1000, "left": 0, "right": 2000, "bal": 0}
        observations.heartbeat(cfg, snapshot)
        wall.return_value = 1299
        observations.heartbeat(cfg, snapshot)
        observations.log.info.assert_called_once()
        self.assertIn("L=unknown R=1.0s balance=0", observations.log.info.call_args.args[0])
        wall.return_value = 1300
        observations.heartbeat(cfg, snapshot)
        self.assertEqual(observations.log.info.call_count, 2)
