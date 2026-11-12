"""Main pet injection and accessory synchronization without real CDP."""

import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import Mock

from wuwa_tappo_plus import __version__, accessories, cdp, config
from wuwa_tappo_plus.config import Config
from wuwa_tappo_plus.pet_page import PetPage, inject_source


def bare_page():
    return PetPage(Mock(target={}, generation=0, document_epoch=0), Mock())


class ReinjectionTests(unittest.TestCase):
    installed = json.dumps({"inject": __version__})

    def runner(self, first_poll):
        runner = bare_page()
        runner.session.js.side_effect = [first_poll, "1", "installed v" + __version__, self.installed]
        return runner

    def test_older_version_is_replaced(self):
        runner = self.runner(json.dumps({"inject": "0.0.0"}))
        runner.poll(Config({}))
        runner.log.info.assert_any_call("replacing injection v0.0.0 with v" + __version__)

    def test_current_injection_is_left_alone(self):
        runner = self.runner(self.installed)
        runner.poll(Config({}))
        self.assertEqual(runner.session.js.call_count, 1)


class AccessoryReconnectTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / accessories.FILE_NAME
        self.overrides = accessories.set_override(self.path, "hat.png", 10, -2, 1.2, 15)
        self.runner = bare_page()
        self.runner.apply_accessories(self.path)
        self.runner.session.js.reset_mock()

    def test_live_anchor_preserves_geometry_with_or_without_layout_size(self):
        for layout in (200, None):
            with self.subTest(layout=layout):
                page = bare_page()
                data = {"base": {"file": "hat.png", "x": -5, "y": 22}, "size": 136}
                expected = dict(data["base"], size=136)
                if layout is not None:
                    data["layout"] = layout
                    expected["layout"] = layout
                page.session.js.return_value = json.dumps(data)
                self.assertEqual(page.accessory_base(), expected)

    def test_restarted_page_receives_overrides_with_the_same_session(self):
        session = self.runner.session
        # Restart/reload cleared the page globals, but connect() reused Session.
        session.js.side_effect = [json.dumps({"inject": 0}), "installed",
                                  json.dumps({"inject": __version__})]
        self.runner.poll(Config({}))
        session.js.side_effect = None
        session.js.reset_mock()
        self.runner.apply_accessories(self.path)
        self.assertIs(self.runner.session, session)
        session.js.assert_called_once_with(
            "window.__WUT_ACCESSORY_OVERRIDES__ = %s; 1"
            % json.dumps(self.overrides, ensure_ascii=False))
        session.js.reset_mock()
        self.runner.apply_accessories(self.path)
        session.js.assert_not_called()

    def test_reconnect_restores_overrides_even_when_injection_is_still_current(self):
        self.runner.session.generation += 1
        self.runner.session.js.return_value = json.dumps({"inject": __version__})
        self.runner.poll(Config({}))
        self.runner.session.js.reset_mock()
        self.runner.apply_accessories(self.path)
        self.runner.session.js.assert_called_once()

    def test_failed_restore_is_retried(self):
        self.runner.session.generation += 1
        self.runner.session.js.side_effect = cdp.CDPError("disconnected")
        with self.assertRaises(cdp.CDPError):
            self.runner.apply_accessories(self.path)
        self.runner.session.js.side_effect = None
        self.runner.session.js.reset_mock()
        self.runner.apply_accessories(self.path)
        self.runner.session.js.assert_called_once()


class InjectionVersionTests(unittest.TestCase):
    """Every page script belongs to the same Plus release."""

    def test_version_is_not_a_user_setting(self):
        self.assertNotIn("version", tomllib.loads(config.DEFAULT_TOML)["inject"])
        self.assertNotIn("version", config.BUILTIN["inject"])
        cfg = Config({"inject": {"enabled": False}})
        self.assertEqual(cfg.inject_cfg["version"], __version__)

    def test_all_rendered_scripts_use_the_product_version(self):
        from wuwa_tappo_plus.rooms import RoomBridge
        from wuwa_tappo_plus.settings import SettingsBridge
        from wuwa_tappo_plus.toasts import ToastBridge

        sources = [inject_source(Config({})), RoomBridge(9222, "127.0.0.1", Mock()).source,
                   SettingsBridge(9222, "127.0.0.1", Mock()).source,
                   ToastBridge(9222, "127.0.0.1", Mock()).source]
        for source in sources:
            self.assertIn(json.dumps(__version__), source)
            self.assertNotIn("__VERSION__", source)
            self.assertNotIn("__CFG__", source)
            self.assertNotIn("/* @modules */", source)
