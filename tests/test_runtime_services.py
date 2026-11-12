"""Resource construction failures and shutdown never strand later resources."""

import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from wuwa_tappo_plus import runtime_services as runtime
from wuwa_tappo_plus.config import Config


class ServiceLifetimeTests(unittest.TestCase):
    def make_services(self, fail=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        names = ("TargetDiscovery", "cdp.Session", "PetPage", "PageObservations",
                 "SettingsBridge", "DailyReport", "RoomBridge", "PlazaBridge",
                 "RoomHistory", "ToastBridge", "ScreenKeeper", "ChestCache",
                 "game_mode.GameModeController")
        self.factories = {}
        for name in names:
            factory = stack.enter_context(patch("wuwa_tappo_plus.runtime_services." + name))
            self.factories[name] = factory
            if name == fail:
                factory.side_effect = OSError("construction failed")
        self.log = Mock()
        return runtime.RuntimeServices(Config({}), self.log, Mock())

    def test_partial_construction_closes_previously_opened_resources(self):
        with self.assertRaises(OSError):
            self.make_services(fail="ScreenKeeper")
        for name in ("cdp.Session", "SettingsBridge", "DailyReport", "RoomBridge",
                     "RoomHistory", "ToastBridge"):
            self.factories[name].return_value.close.assert_called_once_with()
        self.factories["PlazaBridge"].return_value.close.assert_called_once_with(
            self.factories["cdp.Session"].return_value)

    def test_restore_runs_first_and_each_close_failure_leaves_remaining_cleanup_running(self):
        services = self.make_services()
        events = []

        def failing(label):
            def call(*args):
                events.append(label)
                raise OSError(label)
            return call

        services.game_mode.release.side_effect = failing("restore")
        names = ("settings", "reports", "rooms", "history", "plaza", "toasts", "session")
        for name in names:
            getattr(services, name).close.side_effect = failing(name)
        services.close()
        self.assertEqual(events, ["restore", *names])
        services.game_mode.release.assert_called_once_with(services.session)
        services.plaza.close.assert_called_once_with(services.session)
        self.assertEqual(self.log.warn.call_count, 8)

    def test_disconnected_shutdown_leaves_game_mode_recovery_for_next_connection(self):
        services = self.make_services()
        services.session.ws = None
        services.close()
        services.game_mode.release.assert_not_called()
        services.session.close.assert_called_once()
