"""Daemon-side decisions that were previously only visible in the log file."""

import json
import tomllib
import unittest
from unittest.mock import Mock

from wuwa_tappo_plus import __version__, config
from wuwa_tappo_plus.config import Config
from wuwa_tappo_plus.runner import Runner


def bare_runner():
    """A Runner with the file, socket and sqlite side effects left out."""
    runner = object.__new__(Runner)
    runner.log = Mock()
    runner.cfg = Config({})
    runner.session = Mock()
    runner.low_balance_warned_at = 0.0
    return runner


class BalanceWarningTests(unittest.TestCase):
    def ready(self):
        # The left slot came due ten seconds ago.
        return {"now": 1000000, "left": 990000, "right": 1600000, "bal": 740}

    def test_zero_means_the_page_did_not_refuse(self):
        runner = bare_runner()
        runner.check_balance(self.ready(), 0)
        runner.log.warn.assert_not_called()

    def test_unreadable_balance_is_not_a_refusal(self):
        runner = bare_runner()
        runner.check_balance(self.ready(), None)
        runner.log.warn.assert_not_called()

    def test_shortfall_is_reported_once_then_silenced(self):
        runner = bare_runner()
        runner.check_balance(self.ready(), 740)
        runner.log.warn.assert_called_once()
        self.assertIn("740", runner.log.warn.call_args[0][0])
        runner.check_balance(self.ready(), 740)
        runner.log.warn.assert_called_once()

    def test_pending_chest_is_silent(self):
        runner = bare_runner()
        runner.check_balance({"now": 1000000, "left": 1050000, "right": 1600000}, 740)
        runner.log.warn.assert_not_called()


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

        runner = bare_runner()
        sources = [runner.inject_source(), RoomBridge(9222, "127.0.0.1", Mock()).source,
                   SettingsBridge(9222, "127.0.0.1", Mock()).source]
        for source in sources:
            self.assertIn(json.dumps(__version__), source)
            self.assertNotIn("__VERSION__", source)
            self.assertNotIn("__CFG__", source)


if __name__ == "__main__":
    unittest.main()
