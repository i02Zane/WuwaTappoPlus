import unittest
from unittest.mock import patch

from wuwa_tappo_plus import fullscreen

from wuwa_tappo_plus.fullscreen import client_covers_monitor


class ClientCoverTests(unittest.TestCase):
    def test_client_coverage_accepts_tolerance_but_rejects_work_area_and_partial_windows(self):
        monitor = (0, 0, 2560, 1440)
        cases = (
            ("exact", (0, 0, 2560, 1440), True),
            ("title bar", (0, 30, 2560, 1470), False),
            ("maximized work area", (0, 0, 2560, 1392), False),
            ("two pixel tolerance", (-2, -1, 2562, 1441), True),
            ("overscan", (-8, 0, 2568, 1440), True),
            ("right edge missing", (0, 0, 2550, 1440), False),
            ("bottom edge missing", (0, 0, 2560, 1430), False),
            ("smaller window", (0, 0, 1920, 1080), False),
        )
        for label, client, covered in cases:
            with self.subTest(window=label):
                self.assertEqual(client_covers_monitor(client, monitor), covered)


class DetectionFailureTests(unittest.TestCase):
    def test_missing_foreground_is_unknown_instead_of_an_exit(self):
        self.assertIsNone(fullscreen.window_client_covers_monitor(None))

    def test_foreground_api_error_is_unknown(self):
        with patch.object(fullscreen.user32, "GetForegroundWindow", side_effect=OSError("transient")):
            self.assertIsNone(fullscreen.foreground_client_covers_monitor())

    def test_client_rectangle_api_failure_is_unknown(self):
        with patch.object(fullscreen.user32, "GetAncestor", return_value=42), \
                patch.object(fullscreen.user32, "IsWindowVisible", return_value=True), \
                patch.object(fullscreen.user32, "IsIconic", return_value=False), \
                patch.object(fullscreen.user32, "GetClassNameW", return_value=0), \
                patch.object(fullscreen.user32, "GetWindowThreadProcessId", return_value=0), \
                patch.object(fullscreen, "_process_name", return_value="game.exe"), \
                patch.object(fullscreen.user32, "GetClientRect", return_value=False):
            self.assertIsNone(fullscreen.window_client_covers_monitor(42))


class OpenAppsTests(unittest.TestCase):
    def run_picker(self, titles, names, visible=True):
        order = list(titles)
        given = iter(names)

        def enum_windows(callback, _param):
            for hwnd in order:
                callback(hwnd, 0)
            return True

        def get_text(hwnd, buffer, _size):
            buffer.value = titles[hwnd]
            return len(titles[hwnd])

        with patch.object(fullscreen.user32, "EnumWindows", side_effect=enum_windows), \
                patch.object(fullscreen.user32, "IsWindowVisible", return_value=visible), \
                patch.object(fullscreen.user32, "IsIconic", return_value=False), \
                patch.object(fullscreen.user32, "GetWindowLongW", return_value=0), \
                patch.object(fullscreen.user32, "GetWindowTextW", side_effect=get_text), \
                patch.object(fullscreen.user32, "GetWindowThreadProcessId", return_value=0), \
                patch.object(fullscreen, "_process_name", side_effect=lambda pid: next(given)):
            return fullscreen.open_apps()

    def test_lists_one_entry_per_program(self):
        found = self.run_picker({1: "Some game", 2: "Another", 3: "Wuwa Tappo"},
                                ["Game.exe", "game.exe", "WuwaTappo.exe"])
        self.assertEqual(found, [{"name": "Game.exe", "title": "Some game"}])

    def test_hidden_windows_are_not_listed(self):
        self.assertEqual(self.run_picker({1: "Some game"}, ["Game.exe"], visible=False), [])

    def test_entries_without_a_name_are_skipped(self):
        self.assertEqual(self.run_picker({1: "Some game"}, [""]), [])


if __name__ == "__main__":
    unittest.main()
