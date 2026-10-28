import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wuwa_tappo_plus import __version__, cdp
from wuwa_tappo_plus.config import (BUILTIN, DEFAULT_TOML, Config, save_preferences,
                                    sync_config, with_preferences)
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

    def test_default_switches_keep_clicks_running(self):
        self.assertEqual(Config({}).preferences,
                         {"auto_open": True, "balance_reminder": True, "bubble": True, "animation": True,
                          "keep_panel_open": True, "remember_screen": True, "pause_clicks": False, "auto_emote": False})

    def test_balance_reminder_and_bubble_are_saved_independently(self):
        saved = save_preferences({"balance_reminder": False, "bubble": True}, self.path)
        self.assertFalse(saved.balance_reminder)
        self.assertFalse(saved.inject_cfg["balance_reminder"])
        self.assertTrue(saved.alert_bubble)
        saved = save_preferences({"balance_reminder": True, "bubble": False}, self.path)
        self.assertTrue(saved.balance_reminder)
        self.assertFalse(saved.alert_bubble)

    def test_pause_clicks_persists_and_reaches_injection(self):
        saved = save_preferences({"pause_clicks": True}, self.path)
        self.assertTrue(saved.pause_clicks)
        self.assertTrue(saved.inject_cfg["pause_clicks"])
        self.assertTrue(saved.preferences["pause_clicks"])
        self.assertIs(tomllib.loads(self.path.read_text(encoding="utf-8"))["clicks"]["pause"], True)
        self.assertFalse(save_preferences({"pause_clicks": False}, self.path).pause_clicks)

    def test_auto_emote_is_opt_in_persisted_and_reaches_injection(self):
        self.assertFalse(Config({}).auto_emote)
        saved = save_preferences({"auto_emote": True}, self.path)
        self.assertTrue(saved.auto_emote)
        self.assertTrue(saved.inject_cfg["auto_emote"])
        self.assertTrue(saved.preferences["auto_emote"])
        self.assertTrue(tomllib.loads(self.path.read_text(encoding="utf-8"))["clicks"]["auto_emote"])
        self.assertFalse(save_preferences({"auto_emote": False}, self.path).auto_emote)

    def test_screen_memory_switch_is_saved_to_the_panel_section(self):
        saved = save_preferences({"remember_screen": False}, self.path)
        self.assertFalse(saved.panel_remember_screen)
        parsed = tomllib.loads(self.path.read_text(encoding="utf-8"))
        self.assertIs(parsed["panel"]["remember_screen"], False)
        self.assertTrue(parsed["panel"]["keep_open"])

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

    def test_a_config_saved_with_a_bom_is_read_and_rewritten_without_one(self):
        # PowerShell 5.1's `Set-Content -Encoding utf8` writes a BOM and tomllib
        # rejects it as a syntax error, so every read goes through utf-8-sig.
        self.path.write_bytes(b"\xef\xbb\xbf" + DEFAULT_TOML.encode("utf-8"))
        saved = save_preferences({"auto_open": False}, self.path)
        self.assertFalse(saved.auto_open)
        self.assertFalse(self.path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_load_tolerates_a_bom(self):
        from wuwa_tappo_plus import config

        self.path.write_bytes(b"\xef\xbb\xbf" + DEFAULT_TOML.encode("utf-8"))
        with patch.object(config, "CONFIG_PATH", self.path):
            self.assertTrue(config.load().auto_open)

    def test_alert_tiers_follow_the_click_threshold(self):
        self.assertEqual(Config({}).alert_low, 2000)
        self.assertEqual(Config({}).alert_critical, 1000)
        cfg = Config({"inject": {"min_balance": 1500}})
        self.assertEqual(cfg.alert_low, 2500)
        self.assertEqual(cfg.alert_critical, 1500)
        self.assertEqual(cfg.inject_cfg["alert_low"], 2500)
        self.assertEqual(cfg.inject_cfg["alert_critical"], 1500)

    def test_retired_and_unknown_keys_are_ignored(self):
        # An old file still carries these; keeping them must not change
        # behaviour, and saving the switches must not delete them either.
        cfg = Config({"inject": {"poll_ms": 5, "verify_ms": 5, "stale_ms": 5,
                                 "lock_ms": 5, "min_balance": 1200},
                      "alerts": {"low": 1, "critical": 1},
                      "mystery": {"x": 1}})
        self.assertEqual(cfg.inject_cfg["poll_ms"], 1000)
        self.assertEqual(cfg.inject_cfg["verify_ms"], 2000)
        self.assertEqual(cfg.inject_cfg["stale_ms"], 30000)
        self.assertNotIn("lock_ms", cfg.inject_cfg)
        self.assertEqual((cfg.alert_low, cfg.alert_critical), (2200, 1200))
        self.assertEqual(cfg.inject_cfg["min_balance"], 1200)

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

    def test_export_is_a_separate_action_and_retries_do_not_duplicate_zip(self):
        original = self.path.read_bytes()
        with patch("wuwa_tappo_plus.settings.export_logs", return_value=Path("desktop/logs.zip")) as export:
            cfg, update = self.sync_request({"id": "export", "action": "export_logs"})
            self.bridge.session = None
            self.bridge.last_scan = -10
            self.sync_request({"id": "export", "action": "export_logs"})
        export.assert_called_once()
        self.assertEqual(export.call_args.kwargs["data_dir"], self.path.parent)
        self.assertEqual(update["ack"], [{"id": "export", "action": "export_logs", "path": str(Path("desktop/logs.zip"))}])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(cfg.preferences, Config({}).preferences)

    def test_export_failure_acknowledges_error(self):
        with patch("wuwa_tappo_plus.settings.export_logs", side_effect=PermissionError("denied")):
            _, update = self.sync_request({"id": "export", "action": "export_logs"})
        self.assertEqual(update["ack"], [{"id": "export", "action": "export_logs", "error": True}])

    def test_unknown_action_cannot_save_preferences(self):
        original = self.path.read_bytes()
        _, update = self.sync_request({"id": "x", "action": "unknown", "key": "bubble", "value": False})
        self.assertTrue(update["ack"][0]["error"])
        self.assertEqual(self.path.read_bytes(), original)

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


class ConfigSyncTests(unittest.TestCase):
    """The file follows DEFAULT_TOML without losing what the user still uses."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.toml"

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")

    def read(self):
        return self.path.read_text(encoding="utf-8")

    def test_retired_keys_go_and_missing_ones_come_back(self):
        self.write(
            "# my header\n"
            "[app]\n"
            "exe = 'D:\\Pets\\WuwaTappo.exe'  # keep this comment\n"
            "port = 9333\n"
            "\n"
            "[inject]\n"
            "enabled = false\n"
            "poll_ms = 100\n"
            "min_balance = 1500\n"
            "\n"
            "[alerts]\n"
            "# old tier comment\n"
            "low = 3000\n"
            "critical = 2000\n"
            "bubble = false\n"
            "\n"
            "[panel]\n"
            "keep_open = true\n"
        )
        self.assertTrue(sync_config(self.path))
        text = self.read()
        self.assertIn("# my header", text)
        self.assertIn("exe = 'D:\\Pets\\WuwaTappo.exe'  # keep this comment", text)
        self.assertNotIn("poll_ms", text)
        self.assertNotIn("low =", text)
        self.assertNotIn("# old tier comment", text)

        parsed = tomllib.loads(text)
        self.assertEqual(parsed["app"]["port"], 9333)
        self.assertEqual(parsed["inject"], {"enabled": False, "min_balance": 1500})
        self.assertEqual(parsed["alerts"], {"balance": True, "bubble": False, "animation": True})
        self.assertEqual(parsed["rooms"], {"history": 20})       # section appended
        self.assertEqual(parsed["daemon"]["watchdog_min"], 1)    # missing key added

    def test_sync_is_idempotent(self):
        self.write("[inject]\nenabled = true\n")
        self.assertTrue(sync_config(self.path))
        once = self.path.read_bytes()
        self.assertFalse(sync_config(self.path))
        self.assertEqual(self.path.read_bytes(), once)

    def test_a_clean_file_is_not_rewritten(self):
        self.write(DEFAULT_TOML)
        self.assertFalse(sync_config(self.path))

    def test_unknown_sections_are_left_alone(self):
        self.write(DEFAULT_TOML + "\n[mystery]\nx = 1\n")
        sync_config(self.path)
        self.assertEqual(tomllib.loads(self.read())["mystery"], {"x": 1})
        self.assertIn("[mystery]", self.read())

    def test_invalid_toml_raises_instead_of_being_mangled(self):
        self.write("[app]\nport = 1\nport = 2\n")
        with self.assertRaises(tomllib.TOMLDecodeError):
            sync_config(self.path)


class VersionTests(unittest.TestCase):
    def test_javascript_and_bridge_agree_on_the_version(self):
        source = SettingsBridge(9222, "127.0.0.1", Mock()).source
        self.assertIn("var VERSION = " + json.dumps(__version__), source)
        self.assertNotIn("__VERSION__", source)


if __name__ == "__main__":
    unittest.main()
