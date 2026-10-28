"""Confirmed chest timers survive restarts without trusting invalid data."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from wuwa_tappo_plus.chests import ChestCache


class ChestCacheTests(unittest.TestCase):
    def test_save_restore_and_account_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chests.json"
            cache = ChestCache(Mock(), path)
            now = int(time.time() * 1000)
            record = {"account": "a", "left": {"opened_at": now, "ready_at": now + 1800000}}
            cache.observe(record)
            self.assertEqual(ChestCache(Mock(), path).data, record)
            before = path.stat().st_mtime_ns
            cache.observe(record)
            self.assertEqual(path.stat().st_mtime_ns, before)
            cache.observe({"account": None})
            self.assertEqual(cache.data, record)
            cache.observe({"account": "b"})
            self.assertEqual(json.loads(path.read_text()), {"account": "b"})

    def test_invalid_expired_and_future_records_are_ignored(self):
        now = int(time.time() * 1000)
        for opened, ready in [(0, now + 1800000), (now - 2000000, now - 200000),
                              (now + 10000, now + 1810000), ("x", now),
                              (float("nan"), float("inf"))]:
            self.assertEqual(ChestCache.clean({"account": "a", "left": {
                "opened_at": opened, "ready_at": ready}}), {"account": "a"})

    def test_corrupt_file_and_failed_save_do_not_stop_daemon(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chests.json"
            path.write_text("invalid")
            log = Mock()
            cache = ChestCache(log, path)
            self.assertEqual(cache.data, {"account": None})
            cache.path = path / "child.json"
            cache.observe({"account": "a"})
            log.warn.assert_called_once()
            self.assertEqual(cache.data, {"account": None})
