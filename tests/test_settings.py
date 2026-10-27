import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wuwa_tappo_plus import __version__, cdp
from wuwa_tappo_plus.config import BUILTIN, DEFAULT_TOML, Config, save_preferences, with_preferences
from wuwa_tappo_plus.settings import SettingsBridge
from wuwa_tappo_plus.ws import WSError


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.toml"
        self.path.write_text(DEFAULT_TOML, encoding="utf-8")

    def test_template_matches_builtin(self):
        self.assertEqual(tomllib.loads(DEFAULT_TOML), BUILTIN)

    def test_old_config_defaults_all_switches_on(self):
        self.assertEqual(Config({}).preferences,
                         {"auto_open": True, "bubble": True, "animation": True,
                          "keep_panel_open": True})

    def test_save_keeps_other_values_and_comments(self):
        source = "# keep this\n[app]\nexe = 'D:\\Pets\\WuwaTappo.exe'\nport = 9223\n"
        self.path.write_text(source, encoding="utf-8")
        saved = save_preferences({"auto_open": False, "bubble": False,
                                  "animation": False}, self.path)
        self.assertFalse(saved.auto_open)
        self.assertFalse(saved.alert_bubble)
        self.assertFalse(saved.alert_animation)
        self.assertEqual(saved.exe, r"D:\Pets\WuwaTappo.exe")
        self.assertEqual(saved.port, 9223)
        self.assertTrue(self.path.read_text(encoding="utf-8").startswith("# keep this"))
        self.assertEqual(list(self.path.parent.glob(".plus-config-*")), [])

    def test_only_boolean_whitelisted_preferences_are_writable(self):
        original = self.path.read_bytes()
        for data in ({"host": False}, {"auto_open": "false"}, {"bubble": 0}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                save_preferences(data, self.path)
            self.assertEqual(self.path.read_bytes(), original)

    def test_atomic_write_failure_keeps_original(self):
        original = self.path.read_bytes()
        with patch("wuwa_tappo_plus.config.os.replace", side_effect=OSError("locked")), \
                self.assertRaises(OSError):
            save_preferences({"auto_open": False}, self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".plus-config-*")), [])

    def test_live_preferences_do_not_change_connection_settings(self):
        before = Config({"app": {"port": 9223}})
        after = with_preferences(before, {"auto_open": False})
        self.assertTrue(before.auto_open)
        self.assertFalse(after.auto_open)
        self.assertEqual(after.port, 9223)

    def test_panel_preference_is_persisted_and_reloaded(self):
        saved = save_preferences({"keep_panel_open": False}, self.path)
        self.assertFalse(saved.panel_keep_open)
        reloaded = Config(tomllib.loads(self.path.read_text(encoding="utf-8")))
        self.assertFalse(reloaded.preferences["keep_panel_open"])
        self.assertTrue(reloaded.auto_open)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.toml"
        self.path.write_text(DEFAULT_TOML, encoding="utf-8")
        self.log = Mock()
        self.bridge = SettingsBridge(9222, "127.0.0.1", self.log)
        self.bridge.config_path = self.path
        self.target = {"id": "panel", "url": "http://tauri.localhost/index.html#/panel"}

    def sync_request(self, request):
        session = Mock()
        session.target = self.target
        session.js.return_value = json.dumps({"version": __version__, "requests": [request]})
        with patch("wuwa_tappo_plus.settings.cdp.list_targets", return_value=[self.target]), \
                patch("wuwa_tappo_plus.settings.cdp.Session", return_value=session):
            result = self.bridge.sync(Config({}))
        update = session.js.call_args.args[0]
        payload = json.loads(update.removeprefix("window.__WUT_SETTINGS__.update(")
                             .removesuffix("); 1"))
        return result, payload

    def test_change_is_persisted_before_acknowledgement(self):
        cfg, update = self.sync_request({"id": "one", "key": "auto_open", "value": False})
        self.assertFalse(cfg.auto_open)
        self.assertFalse(tomllib.loads(self.path.read_text())["inject"]["enabled"])
        self.assertEqual(update["ack"], [{"id": "one"}])
        self.assertFalse(update["preferences"]["auto_open"])

    def test_failed_save_acknowledges_error_and_preserves_state(self):
        with patch("wuwa_tappo_plus.settings.save_preferences", side_effect=OSError("locked")):
            cfg, update = self.sync_request({"id": "one", "key": "bubble", "value": False})
        self.assertTrue(cfg.alert_bubble)
        self.assertEqual(update["ack"], [{"id": "one", "error": True}])

    def test_unknown_setting_is_rejected(self):
        original = self.path.read_bytes()
        cfg, update = self.sync_request({"id": "one", "key": "port", "value": False})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(update["ack"][0]["error"], True)

    def test_absent_panel_still_loads_live_preferences(self):
        save_preferences({"animation": False}, self.path)
        with patch("wuwa_tappo_plus.settings.cdp.list_targets", return_value=[]):
            cfg = self.bridge.sync(Config({}))
        self.assertFalse(cfg.alert_animation)

    def test_panel_toggle_uses_same_save_and_acknowledgement_path(self):
        cfg, update = self.sync_request({"id": "panel", "key": "keep_panel_open", "value": False})
        self.assertFalse(cfg.panel_keep_open)
        self.assertEqual(update["ack"], [{"id": "panel"}])
        self.assertFalse(update["preferences"]["keep_panel_open"])

    def test_panel_disconnect_does_not_stop_main_daemon(self):
        self.bridge.session = Mock(target=self.target)
        self.bridge.session.js.side_effect = cdp.CDPError("closed")
        with patch("wuwa_tappo_plus.settings.cdp.list_targets", return_value=[self.target]):
            cfg = self.bridge.sync(Config({}))
        self.assertTrue(cfg.auto_open)
        self.assertIsNone(self.bridge.session)

    def test_only_exact_local_panel_route_is_selected(self):
        for url in ("http://example.com/index.html#/panel",
                    "http://tauri.localhost/#/", "http://tauri.localhost/index.html#/login"):
            self.assertFalse(self.bridge._is_panel(url))

    def test_websocket_close_is_a_retryable_cdp_error(self):
        session = cdp.Session(9222)
        session.ws = Mock()
        session.ws.recv.side_effect = WSError("closed")
        with self.assertRaises(cdp.CDPError):
            session.js("1")


class VersionTests(unittest.TestCase):
    def test_javascript_and_bridge_agree_on_the_version(self):
        source = SettingsBridge(9222, "127.0.0.1", Mock()).source
        self.assertIn("var VERSION = " + json.dumps(__version__), source)
        self.assertNotIn("__VERSION__", source)


if __name__ == "__main__":
    unittest.main()
