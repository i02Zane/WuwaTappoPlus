"""Game-mode ownership, debounce and recovery without a live desktop pet."""

import copy
import unittest
from unittest.mock import Mock

from wuwa_tappo_plus import cdp
from wuwa_tappo_plus.game_mode import GameModeController


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.sample = False
        self.state = {"mode": False, "auto": True, "ledger": None, "legacy_auto": None}
        self.calls = []
        self.delay_mode = False
        self.fail_mode = False
        self.log = Mock()
        self.controller = self.make_controller()

    def make_controller(self):
        controller = GameModeController(self.log, clock=lambda: self.now,
                                        wall_clock=lambda: self.now,
                                        detector=lambda: self.sample)
        controller._call = self.bridge
        return controller

    def bridge(self, session, action, value=None):
        self.calls.append((action, copy.deepcopy(value)))
        if action == "read":
            return copy.deepcopy(self.state)
        if action == "save":
            self.state["ledger"] = copy.deepcopy(value)
        elif action == "setAuto":
            self.state["auto"] = value
            return value
        elif action == "setMode":
            if self.fail_mode:
                raise cdp.CDPError("event failed")
            if not self.delay_mode:
                self.state["mode"] = value
                self.state["ledger"]["pending"]["confirmed"] = True
            return self.state["mode"]
        elif action == "release":
            if self.fail_mode:
                raise cdp.CDPError("restore failed")
            ledger = self.state["ledger"]
            if ledger:
                if ledger["owned"]:
                    self.state["mode"] = False
                self.state["auto"] = ledger["original_auto"]
                self.state["ledger"] = None
        return True

    def tick(self, seconds=0, enabled=True):
        self.now += seconds
        self.controller.tick(None, enabled)

    def enter(self):
        self.sample = True
        self.tick()
        self.tick(2)
        self.tick(1)  # Confirm the native value.

    def mode_calls(self):
        return [value for action, value in self.calls if action == "setMode"]

    def test_takeover_does_not_need_a_settings_panel(self):
        self.tick()
        self.assertFalse(self.state["auto"])
        self.assertTrue(self.state["ledger"]["original_auto"])
        self.assertEqual(self.mode_calls(), [])

    def test_only_stable_fullscreen_transitions_change_game_mode(self):
        self.sample = True
        self.tick()
        self.tick(1)
        self.assertEqual(self.mode_calls(), [])
        self.tick(1)
        self.tick(1)
        self.sample = False
        self.tick()
        self.tick(1)
        self.assertTrue(self.state["mode"])
        self.tick(1)
        self.tick(1)
        self.assertEqual(self.mode_calls(), [True, False])
        self.assertFalse(self.state["ledger"]["owned"])

    def test_restarted_daemon_recovers_ownership_from_the_page(self):
        self.enter()
        self.controller = self.make_controller()
        self.sample = False
        self.tick()
        self.tick(2)
        self.assertEqual(self.mode_calls(), [True, False])

    def test_user_exit_is_respected_until_a_new_fullscreen_session(self):
        self.enter()
        self.state["mode"] = False
        self.tick(1)
        self.tick(10)
        self.assertTrue(self.state["ledger"]["suppressed"])
        self.controller = self.make_controller()
        self.tick()
        self.tick(2)
        self.assertEqual(self.mode_calls(), [True])
        self.sample = False
        self.tick()
        self.tick(2)
        self.sample = True
        self.tick()
        self.tick(2)
        self.assertEqual(self.mode_calls(), [True, True])

    def test_manually_enabled_game_mode_is_never_owned_or_switched_off(self):
        self.state["mode"] = True
        self.enter()
        self.sample = False
        self.tick()
        self.tick(2)
        self.tick(enabled=False)
        self.assertTrue(self.state["mode"])
        self.assertEqual(self.mode_calls(), [])

    def test_unknown_and_short_focus_changes_do_not_exit_game_mode(self):
        self.enter()
        self.sample = False
        self.tick()
        self.sample = None
        self.tick(10)
        self.sample = True
        self.tick()
        self.tick(2)
        self.assertEqual(self.mode_calls(), [True])

    def test_waits_for_ack_and_retries_only_the_explicit_target(self):
        self.delay_mode = True
        self.enter()
        self.assertEqual(self.mode_calls(), [True])
        self.tick(1)
        self.assertEqual(self.mode_calls(), [True])
        self.tick(1)
        self.assertEqual(self.mode_calls(), [True, True])
        self.tick(3)
        self.tick(3)
        self.tick(10)
        self.assertEqual(self.mode_calls(), [True, True, True])
        self.assertTrue(self.state["ledger"]["failed"])
        self.log.warn.assert_called_once()

    def test_unacknowledged_emit_is_retried_even_if_storage_already_matches(self):
        self.sample = True
        self.tick()
        self.fail_mode = True
        with self.assertRaises(cdp.CDPError):
            self.tick(2)
        self.state["mode"] = True  # Persistence succeeded, but IPC did not.
        self.fail_mode = False
        self.tick(3)
        self.tick(1)
        self.assertEqual(self.mode_calls(), [True, True])
        self.assertIsNone(self.state["ledger"]["pending"])

    def test_disabling_restores_original_auto_and_only_owned_game_mode(self):
        self.enter()
        self.tick(enabled=False)
        self.assertTrue(self.state["auto"])
        self.assertFalse(self.state["mode"])
        self.assertIsNone(self.state["ledger"])

    def test_original_auto_off_stays_off_after_release(self):
        self.state["auto"] = False
        self.enter()
        self.controller.release(None)
        self.assertFalse(self.state["auto"])

    def test_failed_cleanup_preserves_recovery_state(self):
        self.enter()
        self.fail_mode = True
        with self.assertRaises(cdp.CDPError):
            self.controller.release(None)
        self.assertTrue(self.state["ledger"]["owned"])
        self.fail_mode = False
        self.controller.release(None)
        self.assertTrue(self.state["auto"])

    def test_disabled_feature_does_not_query_the_foreground_window(self):
        self.controller.detector = Mock(side_effect=AssertionError("unexpected detection"))
        self.tick(enabled=False)
        self.controller.detector.assert_not_called()


if __name__ == "__main__":
    unittest.main()
