"""Incomplete module resources must fail before evaluating any page code."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wuwa_tappo_plus import script_source
from wuwa_tappo_plus.settings import SettingsBridge


class ScriptAssemblyTests(unittest.TestCase):
    def test_invalid_or_missing_modules_abort_before_a_page_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(
                json.dumps({"settings.js": ["module.js"]}), encoding="utf-8")
            (root / "module.js").write_text("function feature() {}", encoding="utf-8")
            entry = root / "settings.js"
            with patch.object(script_source, "SCRIPTS_DIR", root), \
                    patch("wuwa_tappo_plus.pages.cdp.Session") as session:
                for text in ("no marker", "/* @modules */ /* @modules */"):
                    entry.write_text(text, encoding="utf-8")
                    with self.subTest(entry=text), self.assertRaises(ValueError):
                        SettingsBridge(9222, "127.0.0.1", Mock())
                entry.write_text("/* @modules */", encoding="utf-8")
                (root / "module.js").unlink()
                with self.assertRaises(FileNotFoundError):
                    SettingsBridge(9222, "127.0.0.1", Mock())
                session.assert_not_called()
