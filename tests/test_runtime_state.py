"""Status and lock behavior, isolated from user state."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wuwa_tappo_plus import __version__
from wuwa_tappo_plus.config import Config
from wuwa_tappo_plus.observations import PageObservations

from wuwa_tappo_plus.runtime_state import InstanceLock, StatePublisher


class StateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "state.json"
        self.publisher = StatePublisher(self.path)

    def publish(self):
        observations = PageObservations(Mock())
        observations.click_count = 4
        observations.alert_level = 2
        self.publisher.publish(Config({}), Mock(target={"url": "pet"}), observations,
                               {"now": 10000, "left": 11000, "right": 0,
                                "bal": 0, "inject": __version__}, {"block": "paused"})
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_existing_status_schema_zero_balance_and_unknown_countdown(self):
        data = self.publish()
        self.assertEqual(data["pid"], os.getpid())
        self.assertEqual(data["plus_version"], __version__)
        self.assertEqual(data["target"], "pet")
        self.assertEqual(data["clicks_logged"], 4)
        self.assertEqual(data["alert"], 2)
        self.assertEqual(data["balance"], 0)
        self.assertEqual(data["remain_left"], 1)
        self.assertIsNone(data["remain_right"])
        self.assertEqual(data["block"], "paused")
        self.assertFalse(self.path.with_suffix(".tmp").exists())
        # Exercise due and absent timers through the status consumed by the CLI.
        self.publisher.publish(Config({}), Mock(target=None), PageObservations(Mock()),
                               {"now": 10000, "left": 8000})
        due = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(due["remain_left"], -2)
        self.assertIsNone(due["remain_right"])

    def test_stopped_mark_is_atomic_and_preserves_last_snapshot(self):
        self.publish()
        with patch("wuwa_tappo_plus.runtime_state.os.replace", wraps=os.replace) as replace:
            self.publisher.stopped()
            replace.assert_called_once_with(self.path.with_suffix(".tmp"), self.path)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIsNone(data["pid"])
        self.assertIn("stopped", data)
        self.assertEqual(data["balance"], 0)
        self.assertEqual(data["clicks_logged"], 4)

    def test_failed_atomic_replace_keeps_original_state(self):
        self.publish()
        previous = self.path.read_bytes()
        with patch("wuwa_tappo_plus.runtime_state.os.replace", side_effect=OSError("locked")):
            self.publisher.stopped()
        self.assertEqual(self.path.read_bytes(), previous)

    def test_missing_or_corrupt_status_is_not_recreated_on_shutdown(self):
        self.publisher.stopped()
        self.assertFalse(self.path.exists())
        for text in ("broken", "[]", "null"):
            self.path.write_text(text, encoding="utf-8")
            self.publisher.stopped()
            self.assertEqual(self.path.read_text(encoding="utf-8"), text)


class LockTests(unittest.TestCase):
    def test_exclusive_instance_lock_can_be_reacquired_after_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daemon.lock"
            first, second = InstanceLock(path), InstanceLock(path)
            self.addCleanup(first.release)
            self.addCleanup(second.release)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            first.release()
            self.assertTrue(second.acquire())
            second.release()
