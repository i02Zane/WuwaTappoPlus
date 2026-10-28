"""Daemon-side decisions that were previously only visible in the log file."""

import json
import time
import tomllib
import unittest
from collections import deque
from unittest.mock import Mock

from wuwa_tappo_plus import __version__, config
from wuwa_tappo_plus.config import Config
from wuwa_tappo_plus.runner import Runner, _remaining


def bare_runner():
    """A Runner with the file, socket and sqlite side effects left out."""
    runner = object.__new__(Runner)
    runner.log = Mock()
    runner.cfg = Config({})
    runner.session = Mock()
    return runner


class ReinjectionTests(unittest.TestCase):
    installed = json.dumps({"inject": __version__})

    def runner(self, first_poll):
        runner = bare_runner()
        runner.cfg = Config({})
        runner.session.js.side_effect = [first_poll, "1", "installed v" + __version__, self.installed]
        return runner

    def test_older_version_is_replaced(self):
        runner = self.runner(json.dumps({"inject": "0.0.0"}))
        runner.ensure_injected()
        runner.log.info.assert_any_call("replacing injection v0.0.0 with v" + __version__)
        message = runner.log.info.call_args_list[0][0][0]
        self.assertIn("v0.0.0", message)
        self.assertIn("v" + __version__, message)

    def test_current_injection_is_left_alone(self):
        runner = self.runner(self.installed)
        runner.ensure_injected()
        self.assertEqual(runner.session.js.call_count, 1)


class PageErrorTests(unittest.TestCase):
    """The page reports a rolling window, so one note arrives on every poll."""

    def test_a_note_is_logged_once_while_it_stays_in_the_window(self):
        runner = bare_runner()
        runner.page_errors = deque(maxlen=400)
        note = {"t": 1000, "m": "click produced no state change"}
        for _ in range(5):
            runner.drain_page_errors([note])
        runner.log.warn.assert_called_once()
        self.assertIn("no state change", runner.log.warn.call_args[0][0])

    def test_distinct_notes_are_each_logged(self):
        runner = bare_runner()
        runner.page_errors = deque(maxlen=400)
        runner.drain_page_errors([{"t": 1, "m": "a"}, {"t": 2, "m": "b"}])
        self.assertEqual(runner.log.warn.call_count, 2)

    def test_non_dict_entries_are_ignored(self):
        runner = bare_runner()
        runner.page_errors = deque(maxlen=400)
        runner.drain_page_errors([None, "x", 3])
        runner.log.warn.assert_not_called()


class BlockedClickTests(unittest.TestCase):
    """A ready chest that never opens must say why."""

    def runner(self):
        runner = bare_runner()
        runner.block_reason = None
        runner.block_key = (None, None, None)
        runner.block_logged_at = 0.0
        runner.last_click_ms = 0
        return runner

    @staticmethod
    def snapshot():
        # The left slot came due ten seconds ago.
        return {"now": 1000000, "left": 990000, "right": 1600000, "bal": 740}

    def test_the_first_reason_carries_balance_and_delay(self):
        runner = self.runner()
        runner.note_block({"reason": "no-button", "balance": 740}, self.snapshot())
        runner.log.warn.assert_called_once()
        message = runner.log.warn.call_args[0][0]
        self.assertIn("no-button", message)
        self.assertIn("balance=740", message)
        self.assertIn("chest 10s overdue", message)

    def test_the_same_reason_is_not_repeated(self):
        runner = self.runner()
        block = {"reason": "no-button", "balance": 740}
        for _ in range(5):
            runner.note_block(block, self.snapshot())
        self.assertEqual(runner.log.warn.call_count, 1)

    def test_a_new_reason_replaces_the_old_one(self):
        runner = self.runner()
        runner.note_block({"reason": "no-button", "balance": 740}, self.snapshot())
        runner.note_block({"reason": "button-disabled", "balance": 740}, self.snapshot())
        self.assertEqual(runner.log.warn.call_count, 2)
        self.assertIn("button-disabled", runner.log.warn.call_args[0][0])

    def test_recovery_is_logged_once(self):
        runner = self.runner()
        runner.note_block({"reason": "no-button", "balance": 740}, self.snapshot())
        runner.note_block(None, self.snapshot())
        runner.note_block(None, self.snapshot())
        self.assertEqual(runner.log.warn.call_count, 1)
        runner.log.info.assert_called_once()
        self.assertIn("open again", runner.log.info.call_args[0][0])

    def test_low_balance_is_reported_like_any_other_gate(self):
        runner = self.runner()
        runner.note_block({"reason": "low-balance", "balance": 740}, self.snapshot())
        runner.log.warn.assert_called_once()
        self.assertIn("low-balance", runner.log.warn.call_args[0][0])
        self.assertEqual(runner.block_reason, "low-balance")

    def test_a_second_chest_gets_its_own_line(self):
        # Two chests land seconds apart: the first must not swallow the second.
        runner = self.runner()
        first = {"reason": "low-balance", "slot": "left", "ts": 1000000, "balance": 508}
        second = {"reason": "low-balance", "slot": "right", "ts": 1005000, "balance": 508}
        runner.note_block(first, self.snapshot())
        runner.note_block(second, {"now": 1012000, "left": 1000000, "right": 1005000})
        self.assertEqual(runner.log.warn.call_count, 2)

    def test_the_same_chest_is_not_logged_twice(self):
        runner = self.runner()
        block = {"reason": "low-balance", "slot": "left", "ts": 1000000,
                 "balance": 508}
        for _ in range(5):
            runner.note_block(block, self.snapshot())
        self.assertEqual(runner.log.warn.call_count, 1)

    def test_giving_up_carries_the_slot_and_attempt_count(self):
        runner = self.runner()
        block = {"reason": "give-up", "slot": "left", "ts": 1000000,
                 "balance": 1508, "attempts": 5}
        runner.note_block(block, self.snapshot())
        runner.log.warn.assert_called_once()
        message = runner.log.warn.call_args[0][0]
        self.assertIn("give-up", message)
        self.assertIn("slot=left", message)
        self.assertIn("after 5 attempts", message)

    def test_a_pause_is_reminded_after_ten_minutes_like_any_stall(self):
        runner = self.runner()
        block = {"reason": "paused", "slot": "left", "ts": 1000000, "balance": 740}
        runner.note_block(block, self.snapshot())
        runner.block_logged_at = time.time() - 601
        runner.note_block(block, self.snapshot())
        self.assertEqual(runner.log.info.call_count, 2)
        runner.log.warn.assert_not_called()

    def test_paused_is_information_not_a_warning(self):
        runner = self.runner()
        runner.note_block({"reason": "paused", "balance": 740}, self.snapshot())
        runner.log.warn.assert_not_called()
        runner.log.info.assert_called_once()

    def test_a_block_that_persists_reminds_after_ten_minutes(self):
        runner = self.runner()
        block = {"reason": "no-button", "balance": 740}
        runner.block_key = (None, "no-button", None)
        runner.block_logged_at = time.time() - 601
        runner.note_block(block, self.snapshot())
        runner.log.warn.assert_called_once()
        self.assertIn("still waiting", runner.log.warn.call_args[0][0])

    def test_the_ui_settling_after_our_click_is_not_a_stall(self):
        runner = self.runner()
        runner.last_click_ms = int(time.time() * 1000) - 1000
        runner.note_block({"reason": "button-disabled", "balance": 2385}, self.snapshot())
        runner.log.warn.assert_not_called()
        self.assertIsNone(runner.block_reason)

    def test_a_block_after_the_click_settled_is_reported(self):
        runner = self.runner()
        runner.last_click_ms = int(time.time() * 1000) - 6000
        runner.note_block({"reason": "button-disabled", "balance": 2385}, self.snapshot())
        runner.log.warn.assert_called_once()


class InjectionVersionTests(unittest.TestCase):
    """Every page script belongs to the same Plus release."""

    def test_version_is_not_a_user_setting(self):
        self.assertNotIn("version", tomllib.loads(config.DEFAULT_TOML)["inject"])
        self.assertNotIn("version", config.BUILTIN["inject"])
        cfg = Config({"inject": {"enabled": False}})
        self.assertEqual(cfg.inject_cfg["version"], __version__)
        self.assertFalse(cfg.auto_open)

    def test_all_rendered_scripts_use_the_product_version(self):
        from wuwa_tappo_plus.rooms import RoomBridge
        from wuwa_tappo_plus.settings import SettingsBridge
        from wuwa_tappo_plus.toasts import ToastBridge

        runner = bare_runner()
        sources = [runner.inject_source(), RoomBridge(9222, "127.0.0.1", Mock()).source,
                   SettingsBridge(9222, "127.0.0.1", Mock()).source,
                   ToastBridge(9222, "127.0.0.1", Mock()).source]
        for source in sources:
            self.assertIn(json.dumps(__version__), source)
            self.assertNotIn("__VERSION__", source)
            self.assertNotIn("__CFG__", source)


class NativeCountdownTests(unittest.TestCase):
    def test_hidden_timer_is_unknown_instead_of_epoch_sized_overdue(self):
        self.assertIsNone(_remaining({"left": 0}, "left", 1793158874936))
        self.assertIsNone(_remaining({}, "right", 1793158874936))

    def test_visible_timer_and_due_button_keep_their_remaining_time(self):
        self.assertEqual(_remaining({"left": 130500}, "left", 100000), 30.5)
        self.assertEqual(_remaining({"right": 98000}, "right", 100000), -2.0)


class HiddenChestBalanceTests(unittest.TestCase):
    def test_disabled_reminders_reset_baseline_without_false_recovery(self):
        runner = bare_runner()
        runner.alert_level = 2
        runner.cfg = Config({"alerts": {"balance": False}})
        runner.alert_balance({"bal": 500}, 0)
        self.assertIsNone(runner.alert_level)
        runner.log.info.assert_not_called()
        runner.log.warn.assert_not_called()
        runner.cfg = Config({})
        runner.alert_balance({"bal": 500}, 2)
        runner.log.warn.assert_not_called()
        runner.alert_balance({"bal": 3000}, 0)
        runner.log.info.assert_called_once()

    def test_hidden_chests_do_not_suppress_balance_changes(self):
        runner = bare_runner()
        runner.alert_level = None
        runner.alert_balance({"left": 0, "right": 0, "bal": 3000}, 0)
        runner.alert_balance({"left": 0, "right": 0, "bal": 0}, 2)
        self.assertEqual(runner.alert_level, 2)
        runner.log.warn.assert_called_once()

    def test_unreadable_wallet_does_not_change_baseline(self):
        runner = bare_runner()
        runner.alert_level = 0
        runner.alert_balance({"left": 100000, "bal": None}, 2)
        self.assertEqual(runner.alert_level, 0)
        runner.log.warn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
