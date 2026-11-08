"""Screen memory: restore the pet's monitor without breaking on missing ones."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wuwa_tappo_plus.screens import ScreenKeeper


def monitor(device, rect, primary=False, name="", hardware=""):
    return {"device": device, "rect": list(rect), "primary": primary,
            "name": name, "id": hardware}


LEFT = monitor("\\\\.\\DISPLAY1", [-3840, 0, 3840, 2160],
               name="Generic PnP Monitor", hardware="MONITOR\\RVM2740")
RIGHT = monitor("\\\\.\\DISPLAY2", [0, 0, 3840, 2160], True,
                name="Generic PnP Monitor", hardware="MONITOR\\SKG2712")


class FakeSession:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def js(self, expression, await_promise=True):
        if self.fail:
            raise OSError("connection closed")
        self.calls.append(expression)
        return "1"


class ScreenKeeperTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "screen.json"
        self.clock = [1000.0]
        self.monitors = [LEFT, RIGHT]
        self.pet = {"hwnd": 1, "device": LEFT["device"]}
        self.session = FakeSession()
        self.moves = []
        self.dispatch_ok = True

        def move(session, hwnd, target):
            self.moves.append((hwnd, dict(target)))
            return self.dispatch_ok

        for patcher in (
            patch("wuwa_tappo_plus.screens.pet_window", lambda: self.pet["hwnd"]),
            patch("wuwa_tappo_plus.screens.list_monitors", lambda: list(self.monitors)),
            patch("wuwa_tappo_plus.screens.monitor_of_window", self.current),
            patch.object(ScreenKeeper, "_settled", return_value=True),
            patch.object(ScreenKeeper, "_dispatch", side_effect=move),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def current(self, _hwnd):
        return next((m for m in self.monitors if m["device"] == self.pet["device"]), None)

    def keeper(self, recorded=None):
        if recorded is not None:
            self.path.write_text(json.dumps(recorded), encoding="utf-8")
        return ScreenKeeper(Mock(), path=self.path, clock=lambda: self.clock[0], settle=2.5)

    def record(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def advance(self, seconds=3.0):
        self.clock[0] += seconds

    def test_the_first_run_remembers_the_current_monitor(self):
        keeper = self.keeper()
        keeper.tick(self.session)
        self.assertEqual(self.record()["device"], LEFT["device"])
        self.assertEqual(self.moves, [])

    def test_the_pet_is_left_alone_on_the_remembered_monitor(self):
        keeper = self.keeper({"device": LEFT["device"], "rect": LEFT["rect"]})
        keeper.tick(self.session)
        self.assertEqual(self.moves, [])

    def test_a_pet_on_another_monitor_is_moved_back(self):
        keeper = self.keeper({"device": LEFT["device"], "rect": LEFT["rect"]})
        self.pet["device"] = RIGHT["device"]
        keeper.tick(self.session)
        self.assertEqual(len(self.moves), 1, "one move request")
        self.assertEqual(self.moves[0][1]["device"], LEFT["device"])

        # The window lands on the remembered one.
        self.advance()
        self.pet["device"] = LEFT["device"]
        keeper.tick(self.session)
        self.assertEqual(len(self.moves), 1, "no second request")
        self.assertEqual(self.record()["device"], LEFT["device"])

    def test_a_missing_monitor_is_never_fatal_and_keeps_the_record(self):
        keeper = self.keeper({"device": LEFT["device"], "rect": LEFT["rect"]})
        self.monitors = [RIGHT]
        self.pet["device"] = RIGHT["device"]
        keeper.tick(self.session)
        self.assertEqual(self.moves, [])
        self.assertEqual(self.record()["device"], LEFT["device"])

    def test_a_monitor_that_comes_back_can_be_restored_again(self):
        keeper = self.keeper({"device": LEFT["device"], "rect": LEFT["rect"]})
        self.monitors = [RIGHT]
        self.pet["device"] = RIGHT["device"]
        keeper.tick(self.session)
        # The display returns while the app keeps running.
        self.monitors = [LEFT, RIGHT]
        keeper.tick(self.session)
        self.assertEqual(len(self.moves), 1, "retry once the monitor is back")

    def test_moving_the_pet_by_hand_updates_the_record_instead_of_fighting_it(self):
        keeper = self.keeper({"device": LEFT["device"], "rect": LEFT["rect"]})
        keeper.tick(self.session)               # same monitor, check recorded
        self.pet["device"] = RIGHT["device"]    # user moves it by hand
        keeper.tick(self.session)
        self.assertEqual(self.moves, [])
        self.assertEqual(self.record()["device"], RIGHT["device"])

    def test_a_restarted_app_is_restored_again(self):
        keeper = self.keeper({"device": LEFT["device"], "rect": LEFT["rect"]})
        keeper.tick(self.session)               # settled on the target
        self.pet = {"hwnd": 2, "device": RIGHT["device"]}
        keeper.tick(self.session)
        self.assertEqual(len(self.moves), 1, "new window on the wrong monitor")

    def test_a_window_that_only_overlaps_the_target_is_moved_again(self):
        keeper = self.keeper({"device": LEFT["device"], "rect": LEFT["rect"],
                              "name": LEFT["name"], "id": LEFT["id"]})
        with patch.object(ScreenKeeper, "_settled", return_value=False):
            keeper.tick(self.session)
        self.assertEqual(len(self.moves), 1,
                         "a window straddling the target is not 'on' it")

    def test_a_breakdown_in_the_switch_request_is_not_fatal(self):
        keeper = self.keeper({"device": LEFT["device"], "rect": LEFT["rect"]})
        self.pet["device"] = RIGHT["device"]
        with patch.object(ScreenKeeper, "_dispatch", return_value=False):
            keeper.tick(self.session)
        self.assertFalse(keeper.restoring)

    def test_the_record_survives_a_broken_file(self):
        self.path.write_text("{not json", encoding="utf-8")
        keeper = ScreenKeeper(Mock(), path=self.path, clock=lambda: self.clock[0])
        keeper.tick(self.session)
        self.assertEqual(self.record()["device"], LEFT["device"])

    def test_the_switch_can_be_turned_off(self):
        keeper = self.keeper({"device": LEFT["device"], "rect": LEFT["rect"],
                              "name": LEFT["name"]})
        self.pet["device"] = RIGHT["device"]
        keeper.tick(self.session, enabled=False)
        self.assertEqual(self.moves, [])
        self.assertEqual(self.record()["device"], LEFT["device"])
        keeper.tick(self.session)
        self.assertEqual(len(self.moves), 1, "works again once re-enabled")

    def test_a_replacement_monitor_on_the_same_output_is_not_confused_for_ours(self):
        keeper = self.keeper({"device": LEFT["device"], "rect": LEFT["rect"],
                              "name": LEFT["name"], "id": LEFT["id"]})
        # The same display output now hosts another monitor: same device name
        # and the same generic friendly name, but a different hardware id.
        self.monitors = [monitor(LEFT["device"], LEFT["rect"],
                                 name=LEFT["name"], hardware="MONITOR\\ANOTHER"), RIGHT]
        keeper.tick(self.session)
        self.assertEqual(self.moves, [])
        self.assertEqual(self.record()["device"], LEFT["device"])
        self.assertEqual(self.record()["id"], LEFT["id"])

    def test_the_monitor_is_found_by_hardware_id_on_another_output(self):
        keeper = self.keeper({"device": LEFT["device"], "rect": LEFT["rect"],
                              "name": LEFT["name"], "id": LEFT["id"]})
        # Replugged into another output: new device name and geometry, same id.
        moved = monitor("\\\\.\\DISPLAY3", [-5000, 0, 3840, 2160],
                        name=LEFT["name"], hardware=LEFT["id"])
        self.monitors = [moved, RIGHT]
        self.pet["device"] = RIGHT["device"]
        keeper.tick(self.session)
        self.assertEqual(len(self.moves), 1)

    def test_a_monitor_outside_the_layout_is_not_a_target(self):
        keeper = self.keeper({"device": LEFT["device"], "rect": LEFT["rect"],
                              "name": LEFT["name"], "id": LEFT["id"]})
        self.monitors = [RIGHT]
        self.pet["device"] = RIGHT["device"]
        keeper.tick(self.session)
        self.assertEqual(self.moves, [])

    def test_identical_monitors_prefer_the_output_we_were_on(self):
        keeper = self.keeper({"device": LEFT["device"], "rect": LEFT["rect"],
                              "name": LEFT["name"], "id": LEFT["id"]})
        twin = monitor("\\\\.\\DISPLAY4", [-9000, 0, 3840, 2160],
                       name=LEFT["name"], hardware=LEFT["id"])
        self.monitors = [twin, LEFT, RIGHT]
        self.assertEqual(keeper._recorded(self.monitors)["device"], LEFT["device"])


class FakeApi:
    """Minimal user32 stand-in for the window geometry call."""

    def __init__(self, rect):
        self.rect = rect

    def GetWindowRect(self, _hwnd, pointer):
        left, top, width, height = self.rect
        pointer._obj.left = left
        pointer._obj.top = top
        pointer._obj.right = left + width
        pointer._obj.bottom = top + height
        return 1


class TargetBoundsTests(unittest.TestCase):
    """Unequal displays: the monitor-sized pet window must be resized too."""

    SMALLER = {"device": "\\.\\DISPLAY2", "rect": [0, 0, 1920, 1080]}

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "screen.json"
        patch("wuwa_tappo_plus.screens.monitor_of_window",
              lambda _hwnd: LEFT).start()
        self.addCleanup(patch.stopall)

    def keeper(self):
        return ScreenKeeper(Mock(), path=self.path, clock=lambda: 0, settle=2.5)

    def test_a_monitor_sized_window_covers_the_target(self):
        patch("wuwa_tappo_plus.screens._api",
              lambda: FakeApi((-3840, 0, 3840, 2160))).start()
        self.assertEqual(self.keeper()._target_bounds(1, self.SMALLER["rect"]),
                         (0, 0, 1920, 1080),
                         "the dock would sit outside a 1080p target if the "
                         "4K window size were kept")

    def test_a_small_window_is_only_centred(self):
        patch("wuwa_tappo_plus.screens._api",
              lambda: FakeApi((-3000, 100, 300, 400))).start()
        self.assertEqual(self.keeper()._target_bounds(1, self.SMALLER["rect"]),
                         (810, 340, 300, 400))


class FakeSession:
    """Page session double: records the expression and returns a fixed reply."""

    def __init__(self, reply="ok"):
        self.reply = reply
        self.calls = []

    def js(self, expression, await_promise=True):
        self.calls.append(expression)
        return self.reply


class DispatchTests(unittest.TestCase):
    """The app's own switch is the only way the window is moved."""

    SMALLER = {"device": "\\.\\DISPLAY2", "rect": [0, 0, 1920, 1080]}

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "screen.json"
        patch("wuwa_tappo_plus.screens.monitor_of_window",
              lambda _hwnd: LEFT).start()
        patch("wuwa_tappo_plus.screens._api",
              lambda: FakeApi((-3840, 0, 3840, 2160))).start()
        self.addCleanup(patch.stopall)

    def keeper(self):
        return ScreenKeeper(Mock(), path=self.path, clock=lambda: 0, settle=2.5)

    def test_the_whole_official_switch_runs(self):
        session = FakeSession("ok")
        self.assertTrue(self.keeper()._dispatch(session, 1, self.SMALLER))
        expression = session.calls[0]
        for step in ("cat-main-monitor-switch-prepare-request",
                     "cat-main-monitor-switch-prepare-done",
                     "set_main_window_monitor_bounds",
                     "cat-main-monitor-switch-layout-request",
                     "cat-main-monitor-switch-layout-done",
                     "sync_aux_windows_to_main_monitor",
                     "cat-display-switched",
                     "finish_main_window_monitor_switch"):
            self.assertIn(step, expression)
        self.assertIn("x: 0, y: 0, width: 1920, height: 1080", expression)

    def test_finish_only_runs_after_the_bounds_command_started(self):
        session = FakeSession("err:monitor switch already in progress")
        self.assertFalse(self.keeper()._dispatch(session, 1, self.SMALLER))
        expression = session.calls[0]
        self.assertIn("if (moved) {", expression,
                      "finishing an unowned switch would release another one")

    def test_without_a_page_nothing_is_moved(self):
        self.assertFalse(self.keeper()._dispatch(None, 1, self.SMALLER))


if __name__ == "__main__":
    unittest.main()
