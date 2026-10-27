"""Room history storage and the panel bridge protocol."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from wuwa_tappo_plus import __version__
from wuwa_tappo_plus.config import Config
from wuwa_tappo_plus.rooms import RoomBridge, RoomHistory, clean_owner, clean_room_id

PANEL = "http://tauri.localhost/index.html#/panel"


class FakeSession:
    """Answers the bridge's evaluations in order and records what it sent."""

    target = {"id": "panel-target", "url": PANEL}

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


def state(version=None, current=None, requests=None, error=None):
    version = __version__ if version is None else version
    return json.dumps({"version": version, "current": current,
                       "requests": requests or [], "error": error})


class RoomIdTests(unittest.TestCase):
    def test_accepts_the_shape_the_panel_renders(self):
        self.assertEqual(clean_room_id("32RH4VrDT"), "32RH4VrDT")
        self.assertEqual(clean_room_id("  32RH4VrDT "), "32RH4VrDT")

    def test_rejects_anything_that_is_not_an_id(self):
        for value in ("", None, "房间号： 32RH4VrDT", "<b>", "a", "x" * 17, "32RH4Vr DT"):
            with self.subTest(value=value):
                self.assertEqual(clean_room_id(value), "")

    def test_owner_is_flattened_and_capped(self):
        self.assertEqual(clean_owner("  白柳\n"), "白柳")
        self.assertEqual(len(clean_owner("主" * 80)), 32)


class RoomHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "rooms.sqlite3"
        self.clock = [datetime(2026, 10, 27, 9, 0, 0)]

    def history(self, limit=20):
        store = RoomHistory(Mock(), path=self.path, now=lambda: self.clock[0], limit=limit)
        self.addCleanup(store.close)
        return store

    def test_first_visit_records_id_host_and_count(self):
        store = self.history()
        self.assertEqual(store.note("32RH4VrDT", "白柳"), "32RH4VrDT")
        rows = store.recent()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["room_id"], "32RH4VrDT")
        self.assertEqual(rows[0]["owner"], "白柳")
        self.assertEqual(rows[0]["visits"], 1)

    def test_returning_to_a_room_counts_another_visit(self):
        store = self.history()
        store.note("32RH4VrDT", "白柳")
        store.note("32RH4VrDT", "白柳")
        self.assertEqual(store.recent()[0]["visits"], 2)

    def test_a_renamed_host_replaces_the_name(self):
        store = self.history()
        store.note("32RH4VrDT", "白柳")
        store.note("32RH4VrDT", "新主人")
        self.assertEqual(store.recent()[0]["owner"], "新主人")

    def test_missing_host_keeps_the_known_one(self):
        store = self.history()
        store.note("32RH4VrDT", "白柳")
        store.note("32RH4VrDT", "")
        self.assertEqual(store.recent()[0]["owner"], "白柳")

    def test_host_rendered_after_the_room_number_is_filled_in(self):
        store = self.history()
        store.note("32RH4VrDT", "")
        self.assertEqual(store.recent()[0]["owner"], "")
        self.assertTrue(store.set_owner("32RH4VrDT", "白柳"))
        row = store.recent()[0]
        self.assertEqual(row["owner"], "白柳")
        self.assertEqual(row["visits"], 1)

    def test_oldest_rooms_fall_out_of_the_list(self):
        store = self.history(limit=3)
        for index in range(5):
            store.note("ROOM%04d" % index, "host%d" % index)
            self.clock[0] = self.clock[0].replace(minute=index)
        rows = store.recent()
        self.assertEqual([row["room_id"] for row in rows],
                         ["ROOM0004", "ROOM0003", "ROOM0002"])

    def test_touch_refreshes_time_without_another_visit(self):
        store = self.history()
        store.note("32RH4VrDT", "白柳")
        self.clock[0] = datetime(2026, 10, 27, 10, 0, 0)
        self.assertTrue(store.touch("32RH4VrDT"))
        row = store.recent()[0]
        self.assertEqual(row["visits"], 1)
        self.assertEqual(row["last_seen"], "2026-10-27T10:00:00")

    def test_forget_removes_one_room(self):
        store = self.history()
        store.note("32RH4VrDT", "白柳")
        store.note("AAAA1111", "别人")
        self.assertTrue(store.forget("32RH4VrDT"))
        self.assertEqual([row["room_id"] for row in store.recent()], ["AAAA1111"])

    def test_resume_refreshes_a_known_room_without_another_visit(self):
        store = self.history()
        store.note("32RH4VrDT", "白柳")
        self.clock[0] = datetime(2026, 10, 27, 12, 0, 0)
        self.assertEqual(store.resume("32RH4VrDT", "白柳"), "32RH4VrDT")
        row = store.recent()[0]
        self.assertEqual(row["visits"], 1)
        self.assertEqual(row["last_seen"], "2026-10-27T12:00:00")

    def test_resume_stores_an_unseen_room_as_one_stay(self):
        store = self.history()
        store.resume("32RH4VrDT", "白柳")
        row = store.recent()[0]
        self.assertEqual((row["visits"], row["owner"]), (1, "白柳"))

    def test_invalid_input_is_not_stored(self):
        store = self.history()
        self.assertEqual(store.note("房间号", "白柳"), "")
        self.assertEqual(store.recent(), [])


class RoomBridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.history = RoomHistory(Mock(), path=Path(self.directory.name) / "rooms.sqlite3",
                                   now=lambda: datetime(2026, 10, 27, 9, 0, 0))
        self.addCleanup(self.history.close)
        self.log = Mock()
        self.bridge = RoomBridge(9222, "127.0.0.1", self.log)
        self.bridge.last_scan = 1e18          # never re-scan inside these tests

    def sync(self, replies, clock=0.0):
        self.bridge.session = FakeSession(replies)
        self.bridge.sync(self.history, clock=lambda: clock)
        return self.bridge.session

    def test_current_room_is_stored_once_per_stay(self):
        session = self.sync([state(current={"room_id": "32RH4VrDT", "owner": "白柳"}), "1"])
        self.assertEqual(len(self.history.recent()), 1)
        self.assertIn("__WUT_ROOMS__.update", session.sent[-1])

    def test_staying_in_the_same_room_does_not_add_visits(self):
        self.sync([state(current={"room_id": "32RH4VrDT", "owner": "白柳"}), "1"])
        self.sync([state(current={"room_id": "32RH4VrDT", "owner": "白柳"}), "1"], clock=30.0)
        self.assertEqual(self.history.recent()[0]["visits"], 1)
        self.sync([state(current={"room_id": "32RH4VrDT", "owner": "白柳"}), "1"], clock=90.0)
        self.assertEqual(self.history.recent()[0]["visits"], 1)

    def test_leaving_and_returning_counts_another_visit(self):
        self.sync([state(current={"room_id": "32RH4VrDT", "owner": "白柳"}), "1"])
        self.sync([state(current=None), "1"], clock=10.0)
        self.sync([state(current={"room_id": "32RH4VrDT", "owner": "白柳"}), "1"], clock=20.0)
        self.assertEqual(self.history.recent()[0]["visits"], 2)

    def test_host_that_renders_late_does_not_add_a_visit(self):
        self.sync([state(current={"room_id": "32RH4VrDT", "owner": ""}), "1"])
        self.sync([state(current={"room_id": "32RH4VrDT", "owner": "白柳"}), "1"], clock=5.0)
        row = self.history.recent()[0]
        self.assertEqual(row["owner"], "白柳")
        self.assertEqual(row["visits"], 1)

    def test_forget_request_is_acknowledged_and_applied(self):
        self.history.note("32RH4VrDT", "白柳")
        request = {"id": "r-1", "action": "forget", "room_id": "32RH4VrDT"}
        session = self.sync([state(requests=[request]), "1"])
        self.assertEqual(self.history.recent(), [])
        payload = session.sent[-1]
        self.assertIn('"id": "r-1"', payload)
        self.assertNotIn('"error"', payload.split("window.__WUT_ROOMS__")[1])

    def test_unknown_action_is_reported_as_a_failure(self):
        request = {"id": "r-2", "action": "launch", "room_id": "32RH4VrDT"}
        session = self.sync([state(requests=[request]), "1"])
        self.assertIn('"error": true', session.sent[-1])

    def test_missing_or_outdated_injection_is_reinstalled(self):
        session = self.sync([
            None,
            "installed v%s" % __version__,
            state(current={"room_id": "32RH4VrDT", "owner": "白柳"}),
            "1",
        ])
        self.log.info.assert_any_call("rooms -> installed v%s" % __version__)
        self.assertIn("__WUT_ROOMS__", session.sent[1])
        self.assertEqual(len(self.history.recent()), 1)

    def test_a_restart_while_in_a_room_does_not_invent_a_visit(self):
        self.sync([state(current={"room_id": "32RH4VrDT", "owner": "白柳"}), "1"])
        # A fresh daemon sees the room you are already sitting in.
        restarted = RoomBridge(9222, "127.0.0.1", self.log)
        restarted.last_scan = 1e18
        restarted.session = FakeSession(
            [state(current={"room_id": "32RH4VrDT", "owner": "白柳"}), "1"])
        restarted.sync(self.history, clock=lambda: 100.0)
        self.assertEqual(self.history.recent()[0]["visits"], 1)

    def test_reconnecting_to_the_same_room_does_not_count(self):
        session = self.sync([state(current={"room_id": "32RH4VrDT", "owner": "白柳"}), "1"])
        session.replies.append("1")           # the uninstall call on close
        self.bridge.close()
        self.sync([state(current={"room_id": "32RH4VrDT", "owner": "白柳"}), "1"], clock=10.0)
        self.assertEqual(self.history.recent()[0]["visits"], 1)

    def test_joining_after_starting_outside_a_room_counts(self):
        self.sync([state(current=None), "1"])
        self.sync([state(current={"room_id": "32RH4VrDT", "owner": "白柳"}), "1"], clock=5.0)
        self.assertEqual(self.history.recent()[0]["visits"], 1)

    def test_a_backend_page_reporting_garbage_cannot_pollute_the_list(self):
        self.sync([state(current={"room_id": "<img src=x>", "owner": "白柳"}), "1"])
        self.assertEqual(self.history.recent(), [])
        self.assertEqual(self.bridge.current, None)


class RoomConfigTests(unittest.TestCase):
    def test_history_limit_comes_from_config(self):
        self.assertEqual(Config({}).room_history, 20)
        self.assertEqual(Config({"rooms": {"history": 5}}).room_history, 5)
        self.assertEqual(Config({"rooms": {"history": 0}}).room_history, 1)
        self.assertIn("rooms", Config({}).data)


if __name__ == "__main__":
    unittest.main()
