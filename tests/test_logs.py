import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from wuwa_tappo_plus.config import Config
from wuwa_tappo_plus.diagnostics import export_logs
from wuwa_tappo_plus.logs import Log, MAX_ENTRY_BYTES


class LogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.path = self.logs / "auto.log"
        self.native = self.root / "official" / "logs"
        native_path = patch("wuwa_tappo_plus.diagnostics._native_log_directory", return_value=self.native)
        native_path.start()
        self.addCleanup(native_path.stop)

    def test_large_unicode_entries_and_retention_remain_bounded(self):
        log = Log(max_bytes=65536, keep=3, path=self.path)
        for i in range(40):
            log.info(str(i) + "中文" * 20000)
        files = list(self.logs.iterdir())
        self.assertEqual(len(files), 4)
        for file in files:
            self.assertLessEqual(file.stat().st_size, 65536)
            for entry in file.read_bytes().splitlines(keepends=True):
                self.assertLessEqual(len(entry), MAX_ENTRY_BYTES)
                self.assertIn("[truncated]", entry.decode("utf-8"))
        self.assertIn("39中文", self.path.read_text(encoding="utf-8"))

    def test_failed_rotation_does_not_grow_current_log(self):
        self.path.write_bytes(b"a" * 65536)
        with patch("wuwa_tappo_plus.logs.os.replace", side_effect=PermissionError("locked")):
            Log(max_bytes=65536, path=self.path).info("new")
        self.assertEqual(self.path.stat().st_size, 65536)

    def test_export_whitelists_files_and_state_without_changing_sources(self):
        self.path.write_text("hello", encoding="utf-8")
        (self.logs / "auto.log.1").write_text("older", encoding="utf-8")
        (self.logs / "auto.log.4").write_text("excluded", encoding="utf-8")
        (self.logs / "secret.txt").write_text("secret", encoding="utf-8")
        (self.root / "config.toml").write_text("token=secret", encoding="utf-8")
        (self.root / ".state.json").write_text(json.dumps({"balance": 100000001, "token": "secret"}))
        destination = export_logs(Config({}), self.root / "out.zip", self.root)
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(set(archive.namelist()), {"logs/auto.log", "logs/auto.log.1", "diagnostics.json", "说明.txt"})
            summary = json.loads(archive.read("diagnostics.json"))
            self.assertEqual(summary["state"]["balance"], 100000001)
            self.assertNotIn("token", summary["state"])
            self.assertEqual(summary["log_limits"]["total_bytes"], 4194304)
        self.assertEqual(self.path.read_text(), "hello")

    def test_no_logs_still_exports_summary(self):
        with zipfile.ZipFile(export_logs(Config({}), self.root / "out.zip", self.root)) as archive:
            self.assertEqual(json.loads(archive.read("diagnostics.json"))["logs"], [])

    def test_existing_destination_is_preserved(self):
        target = self.root / "out.zip"
        target.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            export_logs(Config({}), target, self.root)
        self.assertEqual(target.read_bytes(), b"original")

    def test_export_bounds_oversized_old_files(self):
        self.path.write_bytes(b"x" * 200000)
        cfg = Config({"log": {"max_bytes": 65536}})
        with zipfile.ZipFile(export_logs(cfg, self.root / "out.zip", self.root)) as archive:
            self.assertEqual(len(archive.read("logs/auto.log")), 65536 + MAX_ENTRY_BYTES)
            self.assertTrue(json.loads(archive.read("diagnostics.json"))["logs"][0]["truncated"])

    def test_failed_archive_is_removed(self):
        self.path.write_text("log")
        target = self.root / "out.zip"
        with patch("wuwa_tappo_plus.diagnostics.zipfile.ZipFile.writestr", side_effect=OSError("full")):
            with self.assertRaises(OSError):
                export_logs(Config({}), target, self.root)
        self.assertFalse(target.exists())

    def test_native_logs_are_included_without_account_or_webview_files(self):
        self.native.mkdir(parents=True)
        (self.native / "Wuwa Tappo.log").write_bytes(b"official log")
        (self.native / "Wuwa Tappo.log.1").write_bytes(b"older")
        (self.native / "account.dat").write_bytes(b"secret")
        (self.native / "nested").mkdir()
        (self.native / "nested" / "hidden.log").write_bytes(b"secret")
        with zipfile.ZipFile(export_logs(Config({}), self.root / "out.zip", self.root)) as archive:
            self.assertEqual(archive.read("wuwa-tappo/Wuwa Tappo.log"), b"official log")
            self.assertIn("wuwa-tappo/Wuwa Tappo.log.1", archive.namelist())
            self.assertFalse(any("account" in name or "hidden" in name for name in archive.namelist()))
            self.assertEqual(json.loads(archive.read("diagnostics.json"))["wuwa_tappo"]["status"], "included")
        self.assertEqual((self.native / "Wuwa Tappo.log").read_bytes(), b"official log")

    def test_native_export_has_total_and_per_file_limits(self):
        self.native.mkdir(parents=True)
        for i in range(10):
            (self.native / (str(i) + ".log")).write_bytes(b"x" * 200)
        with patch("wuwa_tappo_plus.diagnostics.NATIVE_FILE_LIMIT", 100), \
                patch("wuwa_tappo_plus.diagnostics.NATIVE_TOTAL_LIMIT", 250):
            with zipfile.ZipFile(export_logs(Config({}), self.root / "out.zip", self.root)) as archive:
                logs = json.loads(archive.read("diagnostics.json"))["wuwa_tappo"]["logs"]
                self.assertEqual(sum(row["bytes"] for row in logs), 250)
                self.assertTrue(all(row["bytes"] <= 100 and row["truncated"] for row in logs))

    def test_missing_native_logs_does_not_prevent_plus_export(self):
        self.path.write_text("plus")
        with zipfile.ZipFile(export_logs(Config({}), self.root / "out.zip", self.root)) as archive:
            self.assertEqual(archive.read("logs/auto.log"), b"plus")
            self.assertEqual(json.loads(archive.read("diagnostics.json"))["wuwa_tappo"]["status"], "missing")

    def test_linked_native_directory_is_skipped(self):
        self.native.mkdir(parents=True)
        with patch("wuwa_tappo_plus.diagnostics._linked", return_value=True):
            with zipfile.ZipFile(export_logs(Config({}), self.root / "out.zip", self.root)) as archive:
                self.assertEqual(json.loads(archive.read("diagnostics.json"))["wuwa_tappo"]["status"], "linked-directory")
