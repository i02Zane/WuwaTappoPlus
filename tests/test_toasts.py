"""Toast capture: every app message becomes one log line, exactly once."""

import json
import unittest
from unittest.mock import Mock, patch

from wuwa_tappo_plus import __version__
from wuwa_tappo_plus.toasts import ToastBridge

TOAST_URL = "http://tauri.localhost/index.html#/toast"


class FakeSession:
    """Answers the bridge's evaluations in order and records what it sent."""

    target = {"id": "toast-target", "url": TOAST_URL}

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.closed = False

    def js(self, expression, await_promise=True):
        self.sent.append(expression)
        if not self.replies:
            raise AssertionError("unexpected evaluation: " + expression[:80])
        return self.replies.pop(0)

    def close(self):
        self.closed = True


def state(entries, version=None):
    return json.dumps({"version": __version__ if version is None else version,
                       "entries": entries})


class ToastBridgeTests(unittest.TestCase):
    def setUp(self):
        self.bridge = ToastBridge(9222, "127.0.0.1", Mock())
        self.bridge.last_scan = 1e18          # never re-scan inside these tests

    def poll(self, replies, **kwargs):
        self.bridge.session = FakeSession(replies)
        return self.bridge.poll(**kwargs)

    def test_a_new_message_is_returned_once(self):
        payload = [{"t": 1000, "m": "宝箱冷却中"}]
        self.assertEqual(self.poll([state(payload)]), [(1000, "宝箱冷却中")])
        self.assertEqual(self.poll([state(payload)]), [])

    def test_messages_buffered_before_the_daemon_started_are_dropped(self):
        payload = [{"t": 100, "m": "旧消息"}, {"t": 900, "m": "新消息"}]
        self.assertEqual(self.poll([state(payload)], since_ms=500), [(900, "新消息")])
        # Still remembered, so a later poll cannot resurface the old one.
        self.assertEqual(self.poll([state(payload)], since_ms=500), [])

    def test_malformed_entries_are_ignored(self):
        payload = [None, 3, {"t": 1}, {"t": 2, "m": 5}, {"t": 3, "m": "ok"}]
        self.assertEqual(self.poll([state(payload)]), [(3, "ok")])

    def test_a_different_script_version_is_replaced(self):
        session = FakeSession([state([], version="0.0.0"),
                               "1",                     # uninstall the old copy
                               "installed v%s" % __version__,
                               state([{"t": 7, "m": "宝箱冷却中"}])])
        self.bridge.session = session
        self.assertEqual(self.bridge.poll(), [(7, "宝箱冷却中")])
        self.bridge.log.info.assert_any_call("toast -> installed v%s" % __version__)
        self.assertIn("MutationObserver", session.sent[2])

    def test_an_empty_page_returns_nothing(self):
        self.assertEqual(self.poll([state([])]), [])

    def test_a_missing_toast_window_is_not_an_error(self):
        self.bridge.last_scan = -1e18
        with patch("wuwa_tappo_plus.toasts.cdp.list_targets", return_value=[]):
            self.assertEqual(self.bridge.poll(), [])
        self.bridge.log.warn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
